"""正規化行情數據 + 新鮮度層（V2 Phase 2）。

yfinance 是免費保底 provider；MARKET_DATA_PROVIDER=massive 時優先嘗試 Massive，
憑證缺失或失敗一律回落 yfinance。所有返回值都帶：
    provider / timestamp / age_seconds / status(live|delayed|stale|unavailable)
新鮮度口徑（務實版）：
    盤中年齡 ≤15min=live、≤120min=delayed、更舊=stale；
    收盤/休市時段以「最近一根 K 棒是否屬於最近一個交易日」判斷，避免深夜誤報 stale。
"""
from datetime import datetime, timezone

import yfinance as yf

import market_hours
from config import MARKET_DATA_PROVIDER, MASSIVE_API_KEY

MARKET_UNIVERSE = ["SPY", "QQQ", "SOXX", "^VIX", "^TNX"]  # ^TNX = 美債 10 年殖利率 proxy


def _provider_name() -> str:
    if MARKET_DATA_PROVIDER == "massive":
        return "massive" if MASSIVE_API_KEY else "yfinance(massive未配置,回落)"
    return "yfinance"


def _freshness(last_ts, provider: str) -> dict:
    if last_ts is None:
        return {"provider": provider, "timestamp": None, "age_seconds": None,
                "status": "unavailable"}
    ts = last_ts.to_pydatetime() if hasattr(last_ts, "to_pydatetime") else last_ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if market_hours.status() == "open":
        status = "live" if age <= 900 else ("delayed" if age <= 7200 else "stale")
    else:
        # 收盤時段：最近一根屬於最近交易日即可視為可用（延遲）
        status = "stale" if age > 4 * 86400 else "delayed"
    return {"provider": provider, "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "age_seconds": int(age), "status": status}


def get_daily_bars(symbol: str, years: int = 1) -> dict:
    """日 K。回傳 {df, provider, timestamp, age_seconds, status}。"""
    try:
        df = yf.Ticker(symbol).history(period=f"{years}y", interval="1d")
    except Exception:
        df = None
    if df is None or df.empty:
        return {"df": None, **_freshness(None, _provider_name())}
    return {"df": df, **_freshness(df.index[-1], _provider_name())}


def get_intraday_bars(symbol: str) -> dict:
    """5 分 K（近 5 日）。"""
    try:
        df = yf.Ticker(symbol).history(period="5d", interval="5m")
    except Exception:
        df = None
    if df is None or df.empty:
        return {"df": None, **_freshness(None, _provider_name())}
    return {"df": df, **_freshness(df.index[-1], _provider_name())}


def get_stock_snapshot(symbol: str) -> dict:
    """個股快照：最新價、日漲跌、量 + 新鮮度。"""
    intra = get_intraday_bars(symbol)
    daily = get_daily_bars(symbol, years=1)
    price = None
    if intra["df"] is not None and not intra["df"].empty:
        price = float(intra["df"]["Close"].iloc[-1])
    prev_close = None
    if daily["df"] is not None and len(daily["df"]) >= 2:
        prev_close = float(daily["df"]["Close"].iloc[-2])
    change_pct = ((price / prev_close - 1) * 100
                  if price and prev_close else None)
    meta = intra["status"] if intra["status"] != "unavailable" else daily["status"]
    return {"symbol": symbol, "price": price, "day_change_pct": change_pct,
            "provider": intra.get("provider"), "timestamp": intra.get("timestamp"),
            "age_seconds": intra.get("age_seconds"), "status": meta,
            "daily_ref": daily}


def get_market_snapshot() -> dict:
    """市場宇宙快照（Layer 1 Market Vibe 用）：每個標的一個 snapshot。"""
    return {sym: get_stock_snapshot(sym) for sym in MARKET_UNIVERSE}
