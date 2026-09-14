"""五層智能評分引擎 + 四個 horizon 模型（V2 Phase 3）。

層級：MARKET VIBE / STOCK PERFORMANCE / SERENITY / EVENTS / OPTIONS
horizon：Tomorrow / Week / Month / Year（各自獨立權重，缺失層「重正規化」而非記零）
鐵律：資料源不可用 → status=unavailable，絕不悄悄當中性。

規則貢獻度按回測驗證結果加權（未驗證規則降權、驗證失敗規則更低；
backtest_rule_weights.json 可覆蓋）。AI 關閉時全部層照常出分（消息面層降級為
「提及統計」，Serenity 立場=unknown）。
"""
from datetime import datetime, timezone

import market_data
import rules
from config import load_watchlist

# 各規則對 Layer 2 的貢獻係數（1.0=足額）：2026-09-12 五年回測結論固化於此
RULE_WEIGHTS = {
    "rsi_os30": 1.0, "rsi_os20": 1.0,              # 驗證通過（超賣反彈）
    "rsi_ob70": 1.0, "rsi_ob80": 1.0,              # 驗證通過（超買回落）
    "macd_golden": 1.0,                            # 驗證通過
    "macd_death": 0.25,                            # 驗證未過
    "ema_golden": 0.25,                            # 驗證未過（過擬合）
    "ema_death": 0.5,
    "trend_below_emas": 0.5,                       # 驗證未過（做空貢獻弱）
    "vol_spike_up": 1.0,                           # 驗證通過
    "vol_spike_down": 0.25,                        # 驗證未過
    "intraday_drop": 1.0, "intraday_spike": 1.0,
    "lev_stop": 1.0,
}
RULE_POINTS = {  # 每條規則的基礎方向分（正=看多貢獻）
    "rsi_os30": +2.0, "rsi_os20": +4.0,
    "rsi_ob70": -2.0, "rsi_ob80": -4.0,
    "macd_golden": +2.0, "macd_death": -2.0,
    "ema_golden": +2.0, "ema_death": -2.0,
    "trend_below_emas": -1.5,
    "vol_spike_up": +1.5, "vol_spike_down": -1.5,
    "intraday_drop": -3.0, "intraday_spike": +3.0,
    "lev_stop": -5.0,
}

HORIZON_WEIGHTS = {  # 百分比；來自 V2 計畫初始值，weight_tuner 可提出候選（owner 審批）
    "tomorrow": {"market": 25, "performance": 35, "serenity": 10, "events": 25, "options": 5},
    "week":     {"market": 20, "performance": 30, "serenity": 15, "events": 25, "options": 10},
    "month":    {"market": 15, "performance": 25, "serenity": 15, "events": 30, "options": 15},
    "year":     {"market": 10, "performance": 20, "serenity": 10, "events": 50, "options": 10},
}
DIRECTION_THRESHOLD = 1.5  # 加權分 |score| ≥1.5 才給方向


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _layer(score, confidence, status, reasons, source_count=0):
    return {"score": round(score, 2), "confidence": round(confidence, 2),
            "status": status, "updated_at": _now(),
            "reasons": reasons, "source_count": source_count}


def _clamp(v, lo=-10.0, hi=10.0):
    return max(lo, min(hi, v))


# ---------- Layer 1: Market Vibe ----------

def market_vibe(snap: dict | None = None) -> dict:
    snap = snap or market_data.get_market_snapshot()

    def _chg(sym):
        s = snap.get(sym)
        return (s["day_change_pct"] if s and s.get("day_change_pct") is not None else None,
                s["price"] if s else None, s["status"] if s else "unavailable")

    parts, reasons, n_ok = [], [], 0
    for sym, w in (("SPY", 1.0), ("QQQ", 0.8), ("SOXX", 0.6)):
        chg, _, st = _chg(sym)
        if chg is None:
            reasons.append(f"{sym} UNAVAILABLE")
            continue
        n_ok += 1
        parts.append(_clamp(chg * 3, -10, 10) * w)
        reasons.append(f"{sym} {chg:+.2f}%")
    vix_chg, vix_price, vix_st = _chg("^VIX")
    vix_component = 0.0
    if vix_price:
        n_ok += 1
        if vix_price < 14:
            vix_component += 4
        elif vix_price > 28:
            vix_component -= 8
        elif vix_price > 20:
            vix_component -= 4
        if vix_chg is not None:
            vix_component -= vix_chg * 0.5
        reasons.append(f"VIX {vix_price:.1f}（{vix_chg:+.1f}%）")
    else:
        reasons.append("VIX UNAVAILABLE")
    tnx_chg, _, _ = _chg("^TNX")
    if tnx_chg is not None:
        n_ok += 1
        parts.append(_clamp(-tnx_chg * 0.8, -6, 6) * 0.5)
        reasons.append(f"US10Y {tnx_chg:+.1f}%")
    else:
        reasons.append("US10Y UNAVAILABLE")
    if n_ok == 0:
        return _layer(0.0, 0.0, "unavailable", ["市場數據源全部不可用"], 0)
    base = sum(parts) / max(sum(1.0 for _ in parts), 1) if parts else 0.0
    score = _clamp(base + vix_component / n_ok * 2)
    return _layer(score, min(0.8, 0.35 + 0.09 * n_ok),
                  "available" if n_ok >= 3 else "stale", reasons, n_ok)


# ---------- Layer 2: Stock Real Performance ----------

def stock_performance(symbol: str, data: dict, market_snap: dict | None = None) -> dict:
    market_snap = market_snap or {}
    signals, summary = rules.evaluate(symbol, data)
    reasons = [s["text"] for s in signals if s["rule_id"] != "none"] or ["無明確技術信號"]

    score = 0.0
    for s in signals:
        rid = s["rule_id"]
        if rid in RULE_POINTS:  # RULE_POINTS 已含方向符號
            score += RULE_POINTS[rid] * RULE_WEIGHTS.get(rid, 0.5)
    # RULE_POINTS 已含方向符號，上面避免重複乘方向：只按 rule_id 加權
    # 相對大盤表現
    spy = market_snap.get("SPY")
    if spy and spy.get("day_change_pct") is not None and summary["daily_change_pct"] is not None:
        rel = summary["daily_change_pct"] - spy["day_change_pct"]
        score += _clamp(rel * 1.5, -4, 4)
        reasons.append(f"相對 SPY {rel:+.1f}pp")
    qqq = market_snap.get("QQQ")
    if qqq and qqq.get("day_change_pct") is not None and summary["daily_change_pct"] is not None:
        rel = summary["daily_change_pct"] - qqq["day_change_pct"]
        score += _clamp(rel * 0.8, -3, 3)
        reasons.append(f"相對 QQQ {rel:+.1f}pp")
    freshness = data.get("freshness", {}).get("intraday", {}).get("status", "delayed")
    conf = {"live": 0.8, "delayed": 0.6, "stale": 0.35}.get(freshness, 0.5)
    return _layer(_clamp(score), conf,
                  "available" if freshness != "stale" else "stale",
                  reasons, source_count=len(signals))


# ---------- Layer 3: Serenity ----------

def serenity_layer(symbol: str, underlying: str) -> dict:
    import serenity as ser
    mentions = ser.recent_mentions(underlying, hours=24 * 60)
    if not mentions:
        return _layer(0.0, 0.9, "available", ["No relevant Serenity thesis"], 0)
    import llm
    if llm.OPENAI_API_KEY:
        corpus = "\n".join(f"[{m['ts'][:10]}] {m['text'][:200]}" for m in mentions[:10])
        out = llm.complete(
            "只根據給定推文判斷分析師 Serenity 對該股票的立場。"
            "只輸出一個 JSON：{\"score\": -10到10的數字(正=看他多), \"reason\": \"繁體中文一句話\"}。"
            "資訊不足給 score 0。不得編造。",
            f"股票：{symbol}\n貼文：\n{corpus}")
        if out:
            import json as _json, re as _re
            m = _re.search(r"\{.*\}", out, _re.DOTALL)
            if m:
                try:
                    obj = _json.loads(m.group(0))
                    return _layer(_clamp(float(obj.get("score", 0))),
                                  0.65, "available",
                                  [str(obj.get("reason", ""))[:120]], len(mentions))
                except (ValueError, TypeError):
                    pass
    return _layer(0.0, 0.3, "available",
                  [f"Serenity 近 60 天提及 {len(mentions)} 次（AI off，立場 unknown）"],
                  len(mentions))


# ---------- Layer 4 / 5：events（Phase 5 提供）與 options（Phase 6 提供）----------

def events_layer(symbol: str, horizon: str) -> dict:
    try:
        import events
        return events.layer_score(symbol, horizon)
    except Exception:
        return _layer(0.0, 0.0, "unavailable", ["事件引擎未啟用"], 0)


def options_layer(symbol: str) -> dict:
    try:
        import options_data
        return options_data.assess(symbol)
    except Exception:
        return _layer(0.0, 0.0, "unavailable", ["期權層未配置"], 0)


# ---------- Horizon 模型 ----------

def _confidence(weighted: float, layers: dict, horizon: str) -> int:
    avail = [l for l in layers.values() if l["status"] in ("available", "stale")]
    if not avail:
        return 0
    strength = min(1.0, abs(weighted) / 6.0)
    same = sum(1 for l in avail if abs(l["score"]) >= 0.5 and
               (l["score"] > 0) == (weighted > 0))
    opposing = sum(1 for l in avail if abs(l["score"]) >= 0.5 and
                   (l["score"] > 0) != (weighted > 0))
    agreement = same / (same + opposing) if (same + opposing) else 0.5
    availability = len(avail) / len(layers)
    freshness = sum(0.5 if l["status"] == "stale" else 1.0 for l in avail) / len(avail)
    conf = int(100 * (0.45 * strength + 0.30 * agreement + 0.15 * availability + 0.10 * freshness))
    direction = "LONG" if weighted >= DIRECTION_THRESHOLD else ("BEARISH" if weighted <= -DIRECTION_THRESHOLD else "NEUTRAL")
    if direction == "NEUTRAL":
        conf = min(conf, 55)
    if horizon == "year":
        ev = layers.get("events", {})
        if ev.get("status") != "available":
            conf = min(conf, 70)  # 基本面/事件資訊弱時封頂
    return min(conf, 90)


def _horizon_output(name: str, layers: dict) -> dict:
    weights = HORIZON_WEIGHTS[name]
    total = sum(w for k, w in weights.items() if layers[k]["status"] != "unavailable")
    if total == 0:
        return {"direction": "NEUTRAL", "confidence": 0, "score": 0.0,
                "main_reason": "全部層級不可用", "main_risk": "數據源故障", "layers": layers}
    weighted = sum(layers[k]["score"] * (weights[k] / total)
                   for k in weights if layers[k]["status"] != "unavailable")
    direction = ("LONG" if weighted >= DIRECTION_THRESHOLD
                 else "BEARISH" if weighted <= -DIRECTION_THRESHOLD else "NEUTRAL")
    ranked = sorted((l for l in layers.values() if l["status"] != "unavailable"),
                    key=lambda l: -abs(l["score"]))
    main_reason = ranked[0]["reasons"][0] if ranked and ranked[0]["reasons"] else "層級信號微弱"
    opposing = [l for l in ranked if (l["score"] > 0) != (weighted > 0) and abs(l["score"]) >= 1.0]
    main_risk = opposing[0]["reasons"][0] if opposing else "多層同向，主要風險為資料時效"
    return {"direction": direction, "confidence": _confidence(weighted, layers, name),
            "score": round(weighted, 2),
            "main_reason": main_reason[:120], "main_risk": main_risk[:120], "layers": layers}


def evaluate_symbol(symbol: str, data: dict | None = None, market_snap: dict | None = None) -> dict:
    """五層 × 四 horizon 完整評估。data 傳入可複用既有抓取；market_snap 可共享。"""
    import data as data_mod
    data = data or data_mod.fetch(symbol)
    market_snap = market_snap or market_data.get_market_snapshot()
    entry = next((i for i in load_watchlist() if i["symbol"] == symbol), None)
    profile = entry["profile"] if entry else "stock"
    underlying = entry["underlying"] if entry else symbol

    vibe = market_vibe(market_snap)
    perf = stock_performance(symbol, data, market_snap)
    ser = serenity_layer(symbol, underlying)
    opt = options_layer(symbol)

    horizons = {}
    for hz in ("tomorrow", "week", "month", "year"):
        ev = events_layer(symbol, hz)
        layers = {"market": vibe, "performance": perf, "serenity": ser,
                  "events": ev, "options": opt}
        horizons[hz] = _horizon_output(hz, layers)
    return {"symbol": symbol, "profile": profile, "generated_at": _now(),
            "price": (float(data["daily"]["Close"].iloc[-1])
                      if data.get("daily") is not None and not data["daily"].empty else None),
            "layers": {"market": vibe, "performance": perf, "serenity": ser,
                       "events": horizons["tomorrow"]["layers"]["events"], "options": opt},
            "horizons": horizons}
