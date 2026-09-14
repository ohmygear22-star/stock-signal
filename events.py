"""結構化事件與政策催化劑引擎（V2 Phase 5）。

來源：Google News RSS（macro.fetch_rss 入庫項）+ 經濟日曆（FED）+ SEC EDGAR（watchlist 公司）。
規範化為統一 schema（event_id/source/category/headline/tickers/direction/relevance/
confirmation_level/time_horizon/url），Layer 4 按 horizon 加權打分。

鐵律：
- Trump/地緣類頭條在未建立 ticker/板塊關聯前，direction 必須為 None（只記錄不打分）
- Trump 確認級數 0-4（媒體報導→演講→白宮聲明→行動宣布→落地生效），影響各 horizon 衰減
- 資料源故障 → status=unavailable，不裝中性
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import macro

BASE = Path(__file__).parent
STATE_FILE = BASE / ".events_state.json"

CATEGORY_RULES = [  # 先專後泛
    ("TRUMP_WHITE_HOUSE", ["trump", "white house", "president ", "executive order", "administration"]),
    ("FED", ["fed ", "fomc", "powell", "rate cut", "rate hike", "interest rate", "federal reserve"]),
    ("SEC", ["sec filing", "securities and exchange", "8-k", "10-q", "10-k", "form 4", "edgar", " sec "]),
    ("EARNINGS", ["earnings", " q1 ", " q2 ", " q3 ", " q4 ", "eps", "results beat", "results miss"]),
    ("GUIDANCE", ["guidance", "outlook cut", "raises forecast", "lowers forecast"]),
    ("REGULATION", ["export control", "chip ban", "antitrust", "regulator", "sanction"]),
    ("GEOPOLITICS", ["iran", "israel", "middle east", "taiwan", "strikes", "missile", "war ", "strait"]),
    ("ANALYST", ["analyst", "price target", "upgrades", "downgrades"]),
    ("LEGAL", ["lawsuit", "sues", "prosecut", "probe", "investigat"]),
    ("MACRO", ["cpi", "inflation", "jobs report", "gdp", "treasury yield", "recession"]),
    ("SUPPLY_CHAIN", ["supply chain", "shortage", "capacity", "wafer", "backlog"]),
]
DEFAULT_CATEGORY = "COMPANY"

NEG_KWS = ["ban", "restrict", "tariff", "lawsuit", "sue", "probe", "downgrade", "miss",
           "cut", "delay", "sanction", "fine", "halt", "recall", "layoff", "falls", "drops",
           "slides", "sinks", "plunge", "warns"]
POS_KWS = ["wins", "contract", "record", "beat", "surge", "approval", "partnership",
           "sold out", "backlog", "buyback", "raises", "rally", "gains", "jumps", "expands"]

TRUMP_CONFIRMATION = [
    (4, ["takes effect", "in force", "implemented", "goes into effect", "now in effect"]),
    (3, ["executive order", "signs", "directs", "administration announces", "ordered"]),
    (2, ["white house statement", "press secretary", "officials announced", "official statement"]),
    (1, ["in a speech", "told reporters", "said in an interview", "speech", "interview", "posted on"]),
    (0, ["reportedly", "sources say", "according to people", "is expected to", "considers"]),
]

SECTOR_KWS = ["chip", "semiconductor", " ai ", "cloud", "data center", "gpu", "optical",
              "photonics", "neocloud", "memory", "tech "]

HZ_FACTOR = {
    "TRUMP_WHITE_HOUSE": {"tomorrow": 1.0, "week": 0.7, "month": 0.35, "year": 0.2},
    "FED": {"tomorrow": 1.0, "week": 0.9, "month": 0.6, "year": 0.4},
    "SEC": {"tomorrow": 0.9, "week": 0.8, "month": 0.7, "year": 0.5},
    "EARNINGS": {"tomorrow": 0.6, "week": 1.0, "month": 0.9, "year": 0.5},
    "GUIDANCE": {"tomorrow": 0.6, "week": 1.0, "month": 0.9, "year": 0.5},
    "GEOPOLITICS": {"tomorrow": 0.9, "week": 0.6, "month": 0.3, "year": 0.2},
    "REGULATION": {"tomorrow": 0.7, "week": 0.8, "month": 1.0, "year": 1.0},
    "ANALYST": {"tomorrow": 0.6, "week": 0.6, "month": 0.5, "year": 0.4},
    "LEGAL": {"tomorrow": 0.6, "week": 0.6, "month": 0.5, "year": 0.4},
    "MACRO": {"tomorrow": 0.8, "week": 0.8, "month": 0.6, "year": 0.4},
    "COMPANY": {"tomorrow": 0.8, "week": 0.9, "month": 0.9, "year": 0.7},
    "COMPETITOR": {"tomorrow": 0.6, "week": 0.7, "month": 0.7, "year": 0.5},
    "SUPPLY_CHAIN": {"tomorrow": 0.8, "week": 0.9, "month": 0.9, "year": 0.7},
}
MARKET_WIDE = {"TRUMP_WHITE_HOUSE", "FED", "MACRO", "GEOPOLITICS"}


def _watch_terms() -> dict[str, list[str]]:
    import serenity
    terms = {}
    for item in _watchlist():
        sym = item["symbol"]
        terms[sym] = [sym] + serenity._aliases().get(item["underlying"], [])
    return terms


def _watchlist():
    from config import load_watchlist
    return load_watchlist()


def _categorize(headline: str) -> str:
    low = " " + headline.lower() + " "
    for cat, kws in CATEGORY_RULES:
        if any(k in low for k in kws):
            return cat
    return DEFAULT_CATEGORY


def _trump_level(headline: str) -> int | None:
    low = headline.lower()
    for level, kws in TRUMP_CONFIRMATION:
        if any(k in low for k in kws):
            return level
    return 1  # 提及總統/白宮但無進展線索 → 至少是言論級


def _direction(headline: str, category: str, relevance: int) -> int | None:
    """政治/地緣類在無 ticker/板塊關聯時不給方向（計畫鐵律）。"""
    if category in MARKET_WIDE and relevance < 55:
        return None
    low = headline.lower()
    if any(k in low for k in NEG_KWS):
        return -6
    if any(k in low for k in POS_KWS):
        return 6
    return None


def normalize(headline: str, source: str = "rss", ts: str | None = None,
              url: str = "") -> dict:
    terms = _watch_terms()
    low = headline.lower()
    tickers = [sym for sym, alts in terms.items()
               if any(re.search(r"\b" + re.escape(a.lower()) + r"\b", low) or
                      f"${sym.lower()}" in low for a in alts)]
    sector_hit = any(k in low for k in SECTOR_KWS)
    if tickers:
        relevance = 90
    elif sector_hit:
        relevance = 55
    elif _categorize(headline) in MARKET_WIDE:
        relevance = 50
    else:
        relevance = 15
    category = _categorize(headline)
    direction = _direction(headline, category, relevance)
    conf = _trump_level(headline) if category == "TRUMP_WHITE_HOUSE" else None
    eid = hashlib.md5((source + headline).encode()).hexdigest()[:12]
    return {"event_id": eid, "timestamp": ts or datetime.now(timezone.utc).isoformat(),
            "source": source, "source_type": "news",
            "category": category, "headline": headline,
            "tickers": tickers, "direction": direction,
            "relevance": relevance, "confirmation_level": conf,
            "time_horizon": list(HZ_FACTOR.get(category, HZ_FACTOR["COMPANY"]).keys()),
            "url": url}


def collect(refresh_rss: bool = True) -> list[dict]:
    """收集 → 規範化 → 去重入庫。回傳全部近期事件。"""
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"seen": [], "events": []}
    seen = set(state.get("seen", []))
    fresh_ids = []
    raw = []

    if refresh_rss:
        items, _ = macro.fetch_rss()
        for it in items:
            raw.append((it["title"], "rss", it["ts"]))
        # 經濟日曆：已排程的 FED/CPI 事件（direction=None，只入上下文）
        for delta, ev in macro.upcoming(10):
            raw.append((f"{ev['label']}（{delta}天後）", "calendar", None))

    events = []
    for headline, src, ts in raw:
        e = normalize(headline, src, ts)
        if e["event_id"] in seen or e["relevance"] < 15:
            continue
        seen.add(e["event_id"])
        fresh_ids.append(e["event_id"])
        events.append(e)
    state["events"] = (events + state.get("events", []))[:300]
    state["seen"] = sorted(seen)[-3000:]
    state["last_success"] = datetime.now(timezone.utc).isoformat() if raw else state.get("last_success")
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))
    return state["events"]


def recent_events_for(symbol: str, days: int = 7) -> list[dict]:
    if not STATE_FILE.exists():
        return []
    state = json.loads(STATE_FILE.read_text())
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for e in state.get("events", []):
        try:
            ts = datetime.fromisoformat(e["timestamp"])
        except ValueError:
            continue
        if ts < cutoff:
            continue
        if symbol in e["tickers"] or (e["category"] in MARKET_WIDE and e["relevance"] >= 50):
            out.append(e)
    return sorted(out, key=lambda e: e["timestamp"], reverse=True)


def layer_score(symbol: str, horizon: str) -> dict:
    """供 scoring.events_layer 調用：按 horizon 加權的事件分。"""
    def _now():
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not STATE_FILE.exists():
        return {"score": 0.0, "confidence": 0.0, "status": "unavailable",
                "updated_at": _now(), "reasons": ["事件引擎尚無數據"], "source_count": 0}
    state = json.loads(STATE_FILE.read_text())
    if not state.get("last_success"):
        return {"score": 0.0, "confidence": 0.0, "status": "unavailable",
                "updated_at": _now(), "reasons": ["事件源不可用"], "source_count": 0}
    evs = recent_events_for(symbol, days=3)
    score, reasons, used = 0.0, [], 0
    for e in evs:
        if e["direction"] is None:
            continue
        factor = HZ_FACTOR.get(e["category"], HZ_FACTOR["COMPANY"])[horizon]
        if e["category"] == "TRUMP_WHITE_HOUSE" and e.get("confirmation_level") is not None:
            factor *= 0.6 + 0.15 * e["confirmation_level"]
        score += e["direction"] * (e["relevance"] / 100.0) * factor
        used += 1
        if len(reasons) < 3:
            tag = f"[{e['category']}] {e['headline'][:60]}"
            if e.get("confirmation_level") is not None:
                tag += f"（確認級 {e['confirmation_level']}）"
            reasons.append(tag)
    score = max(-10.0, min(10.0, score))
    if not reasons:
        reasons = [f"近 3 天無方向性事件（掃過 {len(evs)} 條）"]
    status = "available" if len(evs) > 0 else "stale"
    confidence = min(0.75, 0.25 + 0.1 * used + 0.1 * len(evs))
    return {"score": round(score, 2), "confidence": round(confidence, 2),
            "status": status, "updated_at": _now(),
            "reasons": reasons, "source_count": len(evs)}
