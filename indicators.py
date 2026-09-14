"""技術指標：全部用 pandas 手寫，避免額外依賴。輸入為 yfinance 的 OHLCV DataFrame。"""
import pandas as pd


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder 平滑版的 RSI。"""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    return (100 - 100 / (1 + rs)).fillna(50)


def macd(close: pd.Series) -> dict:
    line = ema(close, 12) - ema(close, 26)
    signal = ema(line, 9)
    return {"line": line, "signal": signal, "hist": line - signal}


def volume_zscore(volume: pd.Series, n: int = 20) -> float:
    """最新一根 K 棒的成交量相對過去 n 根的 z-score。"""
    if len(volume) < n + 1:
        return 0.0
    window = volume.iloc[-(n + 1):-1]
    std = window.std()
    if std == 0 or pd.isna(std):
        return 0.0
    return float((volume.iloc[-1] - window.mean()) / std)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def last_change_pct(close: pd.Series) -> float:
    """最新一根 K 棒的漲跌幅（%）。"""
    if len(close) < 2:
        return 0.0
    return float((close.iloc[-1] / close.iloc[-2] - 1) * 100)


def cross_direction(fast: pd.Series, slow: pd.Series, lookback: int = 3) -> str | None:
    """檢查最近 lookback 根內 fast 是否穿越 slow；回傳 'golden' / 'death' / None。"""
    if len(fast) < lookback + 1:
        return None
    diff = (fast - slow).iloc[-(lookback + 1):]
    for i in range(1, len(diff)):
        prev, cur = diff.iloc[i - 1], diff.iloc[i]
        if prev <= 0 < cur:
            return "golden"
        if prev >= 0 > cur:
            return "death"
    return None
