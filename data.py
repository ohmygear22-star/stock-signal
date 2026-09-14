"""資料層：用 yfinance 拉日 K、盤中 5 分 K、新聞、財報日。全公開資料，不需任何帳戶。"""
from datetime import date
import yfinance as yf


def fetch(symbol: str) -> dict:
    tkr = yf.Ticker(symbol)
    daily = tkr.history(period="6mo", interval="1d")
    if daily.empty:
        raise RuntimeError(f"{symbol}: 拿不到日 K 資料（代碼可能錯誤或資料源異常）")
    intraday = tkr.history(period="5d", interval="5m")
    return {
        "symbol": symbol,
        "daily": daily,
        "intraday": intraday,
        "news": _news(tkr),
        "next_earnings": _next_earnings(tkr),
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
