"""規則引擎：機械買賣信號都在這裡產生。LLM 只做消息面，不做信號本體。

架構：daily_rules_at() 可評估任意歷史 K 棒 i——
  - 回測（backtest.py）用 entry_only=True：條件「首次成立」的瞬間才算一次事件
  - 實盤報告（evaluate）用 entry_only=False：報告當前所處狀態
兩者共用同一套指標公式，避免兩處代碼漂移。"""
from datetime import date

import indicators as ind
from config import (EARNINGS_WARN_DAYS, INTRADAY_CHANGE_PCT,
                    LEV_INTRADAY_CHANGE_PCT, LEV_STOP_PCT)


def build_ctx(daily) -> dict:
    """預先算好全部指標序列，供任意 K 棒評估共用。"""
    close = daily["Close"]
    macd = ind.macd(close)
    return {
        "close": close,
        "volume": daily["Volume"],
        "rsi": ind.rsi(close),
        "macd_line": macd["line"],
        "macd_signal": macd["signal"],
        "ema20": ind.ema(close, 20),
        "ema50": ind.ema(close, 50),
        "atr": ind.atr(daily),
    }


def daily_rules_at(ctx: dict, i: int, entry_only: bool = False,
                   rsi_lo: float = 30.0, rsi_hi: float = 70.0,
                   cross_lookback: int = 0) -> list[dict]:
    """評估第 i 根日 K 的全部日線規則。回傳信號 dict（含 rule_id）。
    entry_only=True：只回報「這一根剛剛成立」的條件（回測事件語義）；
    entry_only=False：條件成立即回報（實盤狀態語義）。
    cross_lookback：實盤可傳 3/5，表示「最近 N 根內發生過穿越」也算（日報語義）。
    i 為正數索引（0-based），需要 i>=1 才能判斷入場。"""
    close, rsi = ctx["close"], ctx["rsi"]
    out, seen = [], set()
    if i < 1 or i >= len(close):
        return out
    prev = i - 1

    def _entered(cur_true: bool, prev_true: bool) -> bool:
        return cur_true and (not prev_true if entry_only else True)

    def _crossed_within(diff, lookback: int) -> bool:
        for j in range(i, max(0, i - lookback), -1):
            if _crossed_at(diff, j):
                return True
        return False

    # --- RSI 超買超賣（回測用單一門檻；實盤分四檔呈現）---
    r_now, r_prev = float(rsi.iloc[i]), float(rsi.iloc[prev])
    if entry_only:
        if _entered(r_now <= rsi_lo, r_prev <= rsi_lo):
            out.append(_s("INFO", "多", f"RSI 跌破 {rsi_lo:.0f} 超賣", rule_id=f"rsi_oversold_{rsi_lo:.0f}"))
        if _entered(r_now >= rsi_hi, r_prev >= rsi_hi):
            out.append(_s("INFO", "空", f"RSI 升破 {rsi_hi:.0f} 超買", rule_id=f"rsi_overbought_{rsi_hi:.0f}"))
    else:
        if r_now >= 80:
            out.append(_s("WARN", "空", f"RSI {r_now:.0f} 嚴重超買，回調風險高", rule_id="rsi_ob80"))
        elif r_now >= 70:
            out.append(_s("INFO", "空", f"RSI {r_now:.0f} 進入超買區", rule_id="rsi_ob70"))
        elif r_now <= 20:
            out.append(_s("WARN", "多", f"RSI {r_now:.0f} 嚴重超賣，反彈機會大", rule_id="rsi_os20"))
        elif r_now <= 30:
            out.append(_s("INFO", "多", f"RSI {r_now:.0f} 進入超賣區", rule_id="rsi_os30"))

    # --- MACD 交叉 ---
    m_diff = ctx["macd_line"] - ctx["macd_signal"]
    if _crossed_within(m_diff, max(1, cross_lookback)):
        kind = "golden" if float(m_diff.iloc[i]) > 0 else "death"
        name = "黃金交叉，短線動能轉強" if kind == "golden" else "死亡交叉，短線動能轉弱"
        out.append(_s("INFO", "多" if kind == "golden" else "空",
                      f"MACD {name}", rule_id=f"macd_{kind}"))

    # --- EMA20/50 交叉 ---
    e_diff = ctx["ema20"] - ctx["ema50"]
    if _crossed_within(e_diff, max(1, cross_lookback)):
        kind = "golden" if float(e_diff.iloc[i]) > 0 else "death"
        name = "EMA20 上穿 EMA50，中期趨勢翻多" if kind == "golden" else "EMA20 下穿 EMA50，中期趨勢翻空"
        out.append(_s("INFO", "多" if kind == "golden" else "空",
                      f"{name}", rule_id=f"ema_{kind}"))

    # --- 價格位於兩條 EMA 之下（趨勢偏空狀態）---
    below_now = float(close.iloc[i]) < min(float(ctx["ema20"].iloc[i]), float(ctx["ema50"].iloc[i]))
    below_prev = float(close.iloc[prev]) < min(float(ctx["ema20"].iloc[prev]), float(ctx["ema50"].iloc[prev]))
    if _entered(below_now, below_prev):
        out.append(_s("INFO", "空", f"價格 ${float(close.iloc[i]):.2f} 同時位於 EMA20/50 之下，趨勢偏空",
                      rule_id="trend_below_emas"))

    # --- 放量異動（成交量 z-score ≥ 2，與前一根比為入場判斷）---
    def _vol_z(j: int) -> float:
        if j < 20:
            return 0.0
        window = ctx["volume"].iloc[j - 20:j]
        std = window.std()
        return (float(ctx["volume"].iloc[j]) - float(window.mean())) / std if std and std > 0 else 0.0

    z, z_prev = _vol_z(i), _vol_z(prev)
    if _entered(z >= 2, z_prev >= 2):
        chg = float(close.iloc[i]) / float(close.iloc[prev]) - 1
        direction, rule = ("多", "vol_spike_up") if chg > 0 else ("空", "vol_spike_down")
        text = (f"放量上漲（成交量 z={z:.1f}），上攻有真實買盤" if chg > 0
                else f"放量下跌（成交量 z={z:.1f}），賣壓沉重")
        out.append(_s("INFO", direction, text, rule_id=rule))

    return out


def evaluate(symbol: str, data: dict, profile: str = "stock") -> tuple[list[dict], dict]:
    """實盤路徑：評估最新一根 K 棒（現有行為保持不變，另補 intraday/財報/槓桿規則）。"""
    daily, intraday = data["daily"], data["intraday"]
    ctx = build_ctx(daily)
    i = len(daily) - 1
    signals = daily_rules_at(ctx, i, entry_only=False, cross_lookback=3)

    close = ctx["close"]
    rsi_now = float(ctx["rsi"].iloc[-1])
    atr_now = float(ctx["atr"].iloc[-1])
    daily_chg = ind.last_change_pct(close)
    vol_z = ind.volume_zscore(daily["Volume"])
    summary = {
        "price": round(float(close.iloc[-1]), 2),
        "daily_change_pct": round(daily_chg, 2),
        "rsi14": round(rsi_now, 1),
        "macd_hist": round(float((ctx["macd_line"] - ctx["macd_signal"]).iloc[-1]), 4),
        "atr_pct": round(atr_now / float(close.iloc[-1]) * 100, 2),
        "volume_z": round(vol_z, 2),
        "ema20": round(float(ctx["ema20"].iloc[-1]), 2),
        "ema50": round(float(ctx["ema50"].iloc[-1]), 2),
    }

    # --- 盤中異動（5 分 K 最新價 vs 前日收盤）---
    if not intraday.empty and len(daily) >= 2:
        prev_close = float(close.iloc[-2]) if _intraday_is_today(intraday, daily) else float(close.iloc[-1])
        intraday_chg = (float(intraday["Close"].iloc[-1]) / prev_close - 1) * 100
        threshold = LEV_INTRADAY_CHANGE_PCT if profile == "leveraged_inverse" else INTRADAY_CHANGE_PCT
        if intraday_chg <= -threshold:
            signals.append(_s("WARN", "空", f"盤中已跌 {abs(intraday_chg):.1f}%，超過 {threshold:.0f}% 異動門檻",
                              rule_id="intraday_drop"))
        elif intraday_chg >= threshold:
            signals.append(_s("WARN", "多", f"盤中已漲 {intraday_chg:.1f}%，超過 {threshold:.0f}% 異動門檻",
                              rule_id="intraday_spike"))

        if profile == "leveraged_inverse":
            if intraday_chg <= -LEV_STOP_PCT:
                signals.append(_s("STOP", "空",
                                  f"⚠️ 盤中虧損已達 {intraday_chg:.1f}%（觸及 {LEV_STOP_PCT:.0f}% 強制止損線），立即評估出場",
                                  rule_id="lev_stop"))
            signals.append(_s("INFO", "中性",
                              "槓桿反向產品提醒：每日重置會造成波動損耗，此信號不建議多日持有；若已隔夜，每多持一天請重新評估 decay 成本",
                              rule_id="lev_decay"))

    if data.get("next_earnings"):
        days_away = (data["next_earnings"] - date.today()).days
        if 0 <= days_away <= EARNINGS_WARN_DAYS:
            signals.append(_s("WARN", "中性",
                              f"財報日就在 {days_away} 天後（{data['next_earnings']}），單日 ±20% 級別波動可能，槓桿倉位應提前減量或離場",
                              rule_id="earnings_near"))

    if not signals:
        signals.append(_s("INFO", "中性", "各項指標無明確信號，維持觀望", rule_id="none"))
    return signals, summary


def _crossed_at(diff, i: int) -> bool:
    """第 i 根是否發生穿越（前一根與本根異號，或由 0 轉正/負）。"""
    if i < 1:
        return False
    prev, cur = float(diff.iloc[i - 1]), float(diff.iloc[i])
    return (prev <= 0 < cur) or (prev >= 0 > cur)


def _intraday_is_today(intraday, daily) -> bool:
    return str(intraday.index[-1].date()) == str(daily.index[-1].date())


def _s(level: str, direction: str, text: str, rule_id: str = "misc") -> dict:
    return {"level": level, "dir": direction, "text": text, "rule_id": rule_id}
