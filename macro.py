"""宏觀事件層：經濟日曆（事前預警）+ RSS 新聞關鍵詞絆網（事中捕捉）+ 日報宏觀面（事後解讀）。

設計（DEPLOYMENT.md §11/§12）：
- macro_calendar.txt：已知日程，一行 =「YYYY-MM-DD [HH:MM(美東)] 描述」
- keywords.txt：關鍵詞絆網詞表（可自由擴充，中英皆可）
- 事件方向不可預測——本層只做預警/解讀/兜底，不產生方向性信號
"""
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import llm

BASE = Path(__file__).parent
CALENDAR_FILE = BASE / "macro_calendar.txt"
KEYWORDS_FILE = BASE / "keywords.txt"
RSS_STATE_FILE = BASE / ".rss_state.json"
ET_TZ = ZoneInfo("America/New_York")
RSS_URL = ("https://news.google.com/rss/search?q=stock+market"
           "&hl=en-US&gl=US&ceid=US:en")

DEFAULT_KEYWORDS = ["Fed", "FOMC", "CPI", "inflation", "rate cut", "rate hike",
                    "tariff", "Trump", "Iran", "Israel", "Middle East", "missile",
                    "oil price", "Treasury", "recession", "bankruptcy"]


# ---------- 經濟日曆 ----------

def load_calendar() -> list[dict]:
    events = []
    if CALENDAR_FILE.exists():
        for raw in CALENDAR_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split(None, 2)
            try:
                ev = {"date": date.fromisoformat(parts[0]), "time": None,
                      "label": parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else "事件")}
            except ValueError:
                continue
            if len(parts) > 2 and ":" in parts[1]:
                hh, mm = parts[1].split(":")[:2]
                if hh.isdigit() and mm.isdigit():
                    ev["time"], ev["label"] = f"{hh}:{mm}", parts[2]
            events.append(ev)
    return sorted(events, key=lambda e: e["date"])


def upcoming(days: int = 14) -> list[tuple[int, dict]]:
    """未來 N 天內的事件（供日報倒數）。"""
    today = date.today()
    out = []
    for ev in load_calendar():
        delta = (ev["date"] - today).days
        if 0 <= delta <= days:
            out.append((delta, ev))
    return out


def is_event_day(d: date | None = None) -> bool:
    d = d or date.today()
    return any(ev["date"] == d for ev in load_calendar())


def in_event_window(now_et: datetime | None = None) -> bool:
    """任一日曆事件的時間點 ±15 分鐘內（daemon 據此把絆網提頻到每分鐘）。"""
    now_et = now_et or datetime.now(ET_TZ)
    for ev in load_calendar():
        if ev["date"] == now_et.date() and ev["time"]:
            hh, mm = ev["time"].split(":")
            start = now_et.replace(hour=int(hh), minute=int(mm)) - timedelta(minutes=15)
            end = now_et.replace(hour=int(hh), minute=int(mm)) + timedelta(minutes=20)
            if start <= now_et <= end:
                return True
    return False


# ---------- RSS 關鍵詞絆網 ----------

def load_keywords() -> list[str]:
    kws = []
    if KEYWORDS_FILE.exists():
        for raw in KEYWORDS_FILE.read_text(encoding="utf-8").splitlines():
            kw = raw.split("#", 1)[0].strip()
            if kw:
                kws.append(kw)
    return kws or DEFAULT_KEYWORDS


def fetch_rss() -> tuple[list[dict], list[dict]]:
    """拉 RSS → (全部最新條目, 其中命中關鍵詞的)。條目去重按 guid。"""
    state = {}
    if RSS_STATE_FILE.exists():
        import json
        state = json.loads(RSS_STATE_FILE.read_text())
    seen = set(state.get("seen", []))

    try:
        resp = requests.get(RSS_URL, timeout=30,
                            headers={"User-Agent": "Mozilla/5.0 (Macintosh)"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception:
        return [], []

    items, hits = [], []
    for node in root.iter("item"):
        guid = (node.findtext("guid") or node.findtext("link") or "").strip()
        title = (node.findtext("title") or "").strip()
        pub = node.findtext("pubDate") or ""
        if not guid or guid in seen:
            continue
        try:
            ts = parsedate_to_datetime(pub).isoformat()
        except Exception:
            ts = datetime.now().isoformat()
        items.append({"guid": guid, "title": title, "ts": ts})
        if _keyword_hit(title):
            hits.append(items[-1])
    if items:
        import json
        state["seen"] = sorted(seen | {i["guid"] for i in items})[-2000:]
        state["recent"] = items[:60]  # 供日報宏觀面分類用
        state["last_fetch"] = datetime.now().isoformat()
        RSS_STATE_FILE.write_text(json.dumps(state))
    return items, hits


def _keyword_hit(title: str) -> bool:
    low = title.lower()
    return any(kw.lower() in low for kw in load_keywords())


# ---------- 日報宏觀面 ----------

def daily_section() -> list[str]:
    """日報的「宏觀面」段落：日曆倒數 + 新聞主題分類（無 key 時退化为標題統計）。"""
    lines = ["◆ 宏觀面"]
    ups = upcoming(14)
    if ups:
        for delta, ev in ups:
            when = f"今天 {ev['time'] or ''}".strip() if delta == 0 else f"{delta} 天後"
            lines.append(f"  ▲ {ev['label']}：{when}")
    else:
        lines.append("  未來兩週無已登記日曆事件（macro_calendar.txt 可補）")

    import json
    headlines = []
    if RSS_STATE_FILE.exists():
        try:
            state = json.loads(RSS_STATE_FILE.read_text())
            cutoff = datetime.now().astimezone() - timedelta(hours=20)
            headlines = [h for h in state.get("recent", [])
                         if datetime.fromisoformat(h["ts"]) >= cutoff]
        except Exception:
            pass
    if headlines:
        titles = "\n".join(f"- {h['title']}" for h in headlines[:25])
        summary = llm.complete(
            "你是宏觀新聞分類器。把給定標題按主題歸類（利率/Fed、地緣/能源、關稅/政治、"
            "流動性/債市、財報/個股、其他），每類最多 2 條，輸出繁體中文短列表，"
            "最後一行用一句話總結今日風險偏向（或「中性」）。不編造。",
            f"今日（近 20 小時）市場新聞標題：\n{titles}",
        ) if llm.OPENAI_API_KEY else None
        if summary:
            lines.extend("  " + s for s in summary.strip().splitlines())
        else:
            lines.append(f"  近 20 小時抓取 {len(headlines)} 條標題（未設 LLM key，跳過分類）")
    return lines
