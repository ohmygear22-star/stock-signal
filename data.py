"""資料層（V2 起經 market_data 正規化；對外返回欄位 daily/intraday/news/next_earnings 不變，
另加 freshness 元數據）。全公開資料，不需任何帳戶。"""
from datetime import date

import market_data
import yfinance as yf


def fetch(symbol: str) -> dict:
    daily_bars = market_data.get_daily_bars(symbol, years=1)
    intraday_bars = market_data.get_intraday_bars(symbol)
    daily, intraday = daily_bars["df"], intraday_bars["df"]
    if daily is None or daily.empty:
        raise RuntimeError(f"{symbol}: 拿不到日 K 資料（代碼可能錯誤或資料源異常）")
    tkr = yf.Ticker(symbol)
    return {
        "symbol": symbol,
        "daily": daily,
        "intraday": intraday if intraday is not None else daily.iloc[0:0],
        "news": _news(tkr),
        "next_earnings": _next_earnings(tkr),
        "freshness": {
            "daily": {k: daily_bars[k] for k in ("provider", "timestamp", "age_seconds", "status")},
            "intraday": {k: intraday_bars[k] for k in ("provider", "timestamp", "age_seconds", "status")},
        },
    }


def _news(tkr: yf.Ticker, limit: int = 12) -> list[dict]:
    items = []
    for n in (tkr.news or [])[:limit]:
        items.append({
            "title": n.get("title", ""),
            "publisher": n.get("publisher", ""),
        })
    return items


def _next_earnings(tkr: yf.Ticker) -> date | None:
    try:
        table = tkr.get_earnings_dates(limit=8)
        if table is None or table.empty:
            return None
        today = date.today()
        for idx in sorted(table.index):
            d = idx.date() if hasattr(idx, "date") else idx
            if d >= today:
                return d
    except Exception:
        pass
    return None
