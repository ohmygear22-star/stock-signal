"""指數震盪絆網：「有大事發生」的最快探測器——不看新聞，看價格。

監控 SPY / QQQ / VIX / ES 期貨（yfinance 免費源）：
- SPY/QQQ 5 分鐘內 ±1% → ⚡（盤中）
- VIX 較當日開盤 +10% → ⚡（盤中）
- ES 期貨相對 6 小時前 ±1% → ⚡（隔夜時段，覆蓋中東式突發）
同類警報 30 分鐘冷卻，防止重複轟炸。daemon 每 2-3 分鐘調一次；
經濟日曆事件窗口內（macro.in_event_window）daemon 提頻到每分鐘。
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

BASE = Path(__file__).parent
STATE_FILE = BASE / ".tripwire_state.json"
COOLDOWN_SEC = 30 * 60

SYMBOLS = {"SPY": None, "QQQ": None, "^VIX": None, "ES=F": None}
IDX_5M_PCT = 1.0      # SPY/QQQ 5 分鐘瞬時門檻
VIX_SURGE_PCT = 10.0  # VIX 相對當日開盤
ES_6H_PCT = 1.0       # ES 期貨 6 小時漂移


def check(force_all: bool = False) -> list[dict]:
    """回傳觸發列表 [{key, message}]。force_all=True 忽略冷卻（測試用）。"""
    now = datetime.now(timezone.utc)
    state = _load_state()
    triggers = []

    for sym in SYMBOLS:
        try:
            df = yf.Ticker(sym).history(period="1d", interval="5m")
        except Exception:
            continue
        if df.empty or len(df) < 2:
            continue
        close = df["Close"]
        last, last_ts = float(close.iloc[-1]), df.index[-1]

        if sym in ("SPY", "QQQ"):
            ref = _price_minutes_ago(close, df.index, 5)
            if ref:
                chg = (last / ref - 1) * 100
                if abs(chg) >= IDX_5M_PCT:
                    triggers.append({"key": f"{sym.lower()}_5m",
                                     "message": f"{sym} 5 分鐘內 {chg:+.1f}%"})
        elif sym == "^VIX":
            day_open = float(df["Open"].iloc[0])
            if day_open > 0:
                chg = (last / day_open - 1) * 100
                if chg >= VIX_SURGE_PCT:
                    triggers.append({"key": "vix_surge",
                                     "message": f"VIX 較開盤 +{chg:.0f}%（恐慌急升）"})
        elif sym == "ES=F":
            ref = _price_minutes_ago(close, df.index, 360)
            if ref:
                chg = (last / ref - 1) * 100
                if abs(chg) >= ES_6H_PCT:
                    triggers.append({"key": "es_6h",
                                     "message": f"ES 期貨 6 小時 {chg:+.1f}%（隔夜異動）"})

    fresh = []
    for t in triggers:
        last_fire = state.get(t["key"], 0)
        if force_all or now.timestamp() - last_fire > COOLDOWN_SEC:
            state[t["key"]] = now.timestamp()
            fresh.append(t)
    if state:
        STATE_FILE.write_text(__import__("json").dumps(state))
    return fresh


def _price_minutes_ago(close, index, minutes: int) -> float | None:
    target = index[-1] - timedelta(minutes=minutes)
    for j in range(len(index) - 1, -1, -1):
        if index[j] <= target:
            return float(close.iloc[j])
    return float(close.iloc[0])


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            import json
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}
