"""Serenity (@aleabitoreddit) 消息面接入層。

資料源（免費、免 X API）：yan-labs/serenity-aleabitoreddit 歸檔倉庫的 tweets JSON，
每小時拉一次增量；新貼文比對監控清單 ticker → 命中即警報 + 記賬。
同時快取最近貼文，供日報 LLM 上下文與 Telegram 查詢（「他最近提過這股嗎」）。
紀律：本層輸出 = 消息面輸入，不是信號本體；絕不跟單。
防假冒：資料只從這一個特定歸檔 URL 取（真號 @aleabitoreddit 的推文歸檔）。"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = Path(__file__).parent
STATE_FILE = BASE / ".serenity_state.json"
CACHE_FILE = BASE / ".serenity_cache.json"
THESES_FILE = BASE / "serenity_theses.jsonl"  # V2：論點跟蹤賬本
ARCHIVE_URL = ("https://raw.githubusercontent.com/yan-labs/"
               "serenity-aleabitoreddit/main/data/aleabitoreddit_tweets.json")
X_USER = "aleabitoreddit"  # SERENITY_PROVIDER=x 時的官方數據目標（需 X_BEARER_TOKEN）


def fetch_new_x() -> tuple[list[dict], str] | None:
    """官方 X API 路徑（SERENITY_PROVIDER=x 且有 token）。失敗回 None → 上層回落 archive。"""
    from config import SERENITY_PROVIDER, X_BEARER_TOKEN
    if SERENITY_PROVIDER != "x" or not X_BEARER_TOKEN:
        return None
    try:
        resp = requests.get(
            f"https://api.x.com/2/users/by/username/{X_USER}/tweets",
            headers={"Authorization": f"Bearer {X_BEARER_TOKEN}"},
            params={"max_results": 25, "tweet.fields": "created_at"},
            timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", [])
        return ([{"id": str(t["id"]), "text": t["text"], "ts": t["created_at"]}
                 for t in data], "OK（官方 X API）")
    except Exception:
        return None


# ---------- V2：論點分類與跟蹤 ----------

# 確定性關鍵詞（AI off 時的唯一分類依據；命中多類取最先）
DETERMINISTIC_RULES = [
    ("REDUCE_EXIT", -1, ["sold my", "trimmed", "took profit", "exited", "closed my", "sold all", "zero position"]),
    ("CONVICTION_UP", +1, ["adding", "bought more", "tripled", "doubled my", "highest conviction", "largest position"]),
    ("CONVICTION_DOWN", -1, ["downgrade", "cut to", "reducing", "trimmed my"]),
    ("NEW_THESIS", 0, ["new thesis", "initiating", "opening a position", "starting a position"]),
    ("CATALYST_UPDATE", 0, ["catalyst", "earnings", "backlog", "contract", "order win", "sold out", "capacity"]),
    ("THESIS_UPDATE", 0, ["update:", "thesis update", "revisited", "re-rating"]),
]


def classify_deterministic(text: str) -> tuple[str, int | None]:
    low = text.lower()
    for label, stance_hint, kws in DETERMINISTIC_RULES:
        if any(k in low for k in kws):
            return label, stance_hint
    return ("CASUAL_COMMENT" if low.startswith("@") else "UNKNOWN"), None


def classify_ai(text: str, symbol: str) -> dict | None:
    """AI ON：立場/類型/信念/時間尺度。必須引用原文，輸出 JSON。"""
    import llm
    if not llm.OPENAI_API_KEY:
        return None
    out = llm.complete(
        "分析這條分析師 Serenity 的推文對指定股票的含義。只輸出 JSON："
        '{"classification": "NEW_THESIS|THESIS_UPDATE|CONVICTION_UP|CONVICTION_DOWN|'
        'CATALYST_UPDATE|REDUCE_EXIT|CASUAL_COMMENT", '
        '"stance": -10到10(正=看他多), "conviction": 0到10, "horizon": "short|mid|long"}。'
        "資訊不足時 classification 用 CASUAL_COMMENT。不得編造。",
        f"股票：{symbol}\n推文：{text[:1500]}")
    if not out:
        return None
    import json as _json, re as _re
    m = _re.search(r"\{.*\}", out, _re.DOTALL)
    if not m:
        return None
    try:
        return _json.loads(m.group(0))
    except ValueError:
        return None


def track_mention(hit: dict) -> list[dict]:
    """把命中貼文寫入論點跟蹤賬本（每 ticker 一條），並回傳「有意義」事件（供 ledger）。
    hit = {id, text, ts, symbols: [...]}"""
    records, meaningful = [], []
    known_ids = set()
    if THESES_FILE.exists():
        for line in THESES_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    known_ids.add(r["post_id"] + "|" + r["ticker"])
                except ValueError:
                    pass
    for sym in hit["symbols"]:
        key = f"{hit['id']}|{sym}"
        new_vs_repeat = "repeat" if key in known_ids else "new"
        det_class, det_stance = classify_deterministic(hit["text"])
        rec = {"post_id": hit["id"], "timestamp": hit["ts"], "ticker": sym,
               "text": hit["text"][:600], "source": "archive",
               "classification": det_class, "stance": det_stance,
               "conviction": None, "new_vs_repeat": new_vs_repeat}
        if new_vs_repeat == "new":
            ai = classify_ai(hit["text"], sym)
            if ai:
                rec["classification"] = ai.get("classification", det_class)
                rec["stance"] = ai.get("stance", det_stance)
                rec["conviction"] = ai.get("conviction")
            with THESES_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            records.append(rec)
            if rec["classification"] not in ("CASUAL_COMMENT", "UNKNOWN"):
                meaningful.append(rec)
    return records, meaningful


def thesis_summary(symbol: str, days: int = 60) -> str:
    """近 N 天該 ticker 的論點跟蹤摘要（一行版，供日報/查詢）。"""
    from datetime import datetime as _dt, timedelta as _td
    if not THESES_FILE.exists():
        return "無跟蹤記錄"
    cutoff = _dt.now(timezone.utc) - _td(days=days)
    rows = []
    for line in THESES_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("ticker") != symbol:
            continue
        try:
            ts = _dt.fromisoformat(r["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts >= cutoff:
            rows.append(r)
    if not rows:
        return "近 {d} 天無論點事件".format(d=days)
    parts = [f"{r['classification']}({r['ts'] if 'ts' in r else r['timestamp'][:10]})"
             for r in rows[-5:]]
    return f"{len(rows)} 個論點事件：" + "、".join(parts)

# 監控 ticker 的口語別名（ticker 本身自動比對，含 $TICKER 形式）；可用 aliases.txt 擴充
DEFAULT_ALIASES = {
    "CRWV": ["CoreWeave"],
    "NVDA": ["Nvidia", "NVIDIA"],
    "AAPL": ["Apple"],
    "IREN": [],
    "POET": ["POET Technologies"],
}
CACHE_KEEP = 300  # 快取保留最近 N 條


def _aliases() -> dict:
    aliases = {k: list(v) for k, v in DEFAULT_ALIASES.items()}
    f = BASE / "aliases.txt"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                parts = line.split()
                aliases.setdefault(parts[0].upper(), []).extend(p.strip(",") for p in parts[1:])
    return aliases


def _norm_tweet(raw: dict) -> dict | None:
    """歸檔欄位防禦性解析：不同版本的欄位名都試。"""
    tid = raw.get("id") or raw.get("tweet_id") or raw.get("tweetId")
    text = raw.get("text") or raw.get("full_text") or raw.get("content") or ""
    ts = (raw.get("createdAtISO") or raw.get("createdAt") or
          raw.get("timestamp") or raw.get("created_at") or raw.get("time"))
    if tid is None or not text:
        return None
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return {"id": str(tid), "text": text, "ts": str(ts)}


def fetch_new() -> tuple[list[dict], str]:
    """拉歸檔，回傳 (新貼文列表, 狀態消息)。失敗回傳 ([], 錯誤消息)——上層負責健康標記。"""
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"seen": []}
    seen = set(state.get("seen", []))
    try:
        resp = requests.get(ARCHIVE_URL, timeout=90)
        resp.raise_for_status()
        raw_list = resp.json()
        if isinstance(raw_list, dict):  # 某些版本把列表包在鍵下
            raw_list = next((v for v in raw_list.values() if isinstance(v, list)), [])
    except Exception as exc:
        return [], f"歸檔拉取失敗：{exc}"

    tweets = [t for t in (_norm_tweet(r) for r in raw_list) if t]
    if not state.get("seen"):
        # 首次運行：全部視為已見（基線），只對「未來」的新貼警報
        fresh = []
    else:
        fresh = [t for t in tweets if t["id"] not in seen]

    # seen 保留全量 id（歸檔 ~6.5k 條，可控），避免舊貼被反覆當新貼
    state["seen"] = sorted(seen | {t["id"] for t in tweets})
    state["last_fetch"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state))
    CACHE_FILE.write_text(json.dumps(tweets[:CACHE_KEEP], ensure_ascii=False))
    return fresh, f"OK（歸檔 {len(tweets)} 條，新 {len(fresh)} 條）"


def match_watchlist(tweets: list[dict], watchlist: list[str]) -> list[dict]:
    """回傳命中監控清單的貼文（附命中的 symbol 列表）。"""
    aliases = _aliases()
    patterns = {}
    for sym in watchlist:
        alts = [sym] + aliases.get(sym, [])
        patterns[sym] = re.compile(
            r"(?:" + "|".join(re.escape(a) for a in alts) + r")",
            re.IGNORECASE,
        )
    hits = []
    for t in tweets:
        matched = [sym for sym, pat in patterns.items() if pat.search(t["text"])]
        if matched:
            hits.append({**t, "symbols": matched})
    return hits


def recent_mentions(symbol: str, hours: int = 72) -> list[dict]:
    """近 N 小時提及某 ticker 的貼文（供日報上下文 / Telegram 查詢）。"""
    if not CACHE_FILE.exists():
        return []
    try:
        tweets = json.loads(CACHE_FILE.read_text())
    except json.JSONDecodeError:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    sym_pattern = re.compile(re.escape(symbol), re.IGNORECASE)
    out = []
    for t in tweets:
        try:
            ts = datetime.fromisoformat(t["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts >= cutoff and sym_pattern.search(t["text"]):
            out.append(t)
    return out


def health() -> str:
    """--check / /status 用：最近一次成功拉取時間。"""
    if not STATE_FILE.exists():
        return "從未拉取"
    state = json.loads(STATE_FILE.read_text())
    return f"上次拉取 {state.get('last_fetch', '?')[:16]}"
