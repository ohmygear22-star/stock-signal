"""Telegram V2 推送策略（V2 Phase 7）：只在「有意義的變化」時說話。

規則（預設）：
- 首次評估 → 推送基線
- 任一 horizon 方向改變 → 推送（高置信時用 CRITICAL 格式）
- 任一 horizon 置信度變動 ≥10pp → 推送
- 出現關鍵事件（Trump/Fed/SEC 級別）→ 推送
- 論點失效（main_risk 變成現實 / 方向反轉）→ 併入方向改變
- 其餘一切 → 不打擾
狀態存 .push_state.json（每個 symbol 上一輪 horizon 摘要）。
"""
import json
from pathlib import Path

STATE_FILE = Path(__file__).parent / ".push_state.json"
CONFIDENCE_DELTA_PP = 10
HZ_LABEL = {"tomorrow": "明日", "week": "本週", "month": "本月", "year": "今年"}
DIR_ICON = {"LONG": "🟢", "NEUTRAL": "🟡", "BEARISH": "🔴"}


def _load() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))


def summarize(evaluation: dict) -> dict:
    return {hz: {"direction": h["direction"], "confidence": h["confidence"]}
            for hz, h in evaluation["horizons"].items()}


def decide(symbol: str, evaluation: dict, critical_event: dict | None = None) -> dict:
    """回傳 {"push": bool, "critical": bool, "changes": ["明日 66% → 71%", ...]}。"""
    state = _load()
    new = summarize(evaluation)
    old = state.get(symbol, {}).get("horizons")
    changes = []
    critical = False

    if old is None:
        changes.append("首次基線評估")
        should = True
    else:
        should = False
        for hz in new:
            o, n = old.get(hz), new[hz]
            if o is None:
                continue
            if o["direction"] != n["direction"]:
                changes.append(f"{HZ_LABEL[hz]} {o['direction']} → {n['direction']}")
                should = True
                if n["confidence"] >= 65 or o["confidence"] >= 65:
                    critical = True
            # 2026-09-15 owner 指令：置信度變動不再觸發推送（含 Year——任何 horizon
            # 只有方向改變才推）。常規更新改由每日三段定時摘要承擔。
    if critical_event:
        should = True
        critical = critical or critical_event.get("level", 3) >= 3
        changes.append("⚡ " + critical_event.get("headline", "關鍵事件")[:80])

    state[symbol] = {"horizons": new,
                     "ts": evaluation.get("generated_at")}
    _save(state)
    return {"push": should, "critical": critical, "changes": changes}


def _layer_icon(score: float, status: str) -> str:
    if status == "unavailable":
        return "⚪"
    if score >= 1.5:
        return "🟢"
    if score <= -5:
        return "🔴"
    if score <= -1.5:
        return "🟠"
    return "🟡"


LAYER_LABEL = {"market": "Market", "performance": "Performance", "serenity": "Serenity",
               "events": "Events", "options": "Options"}


def format_today_digest(evaluations: list[dict], slot_label: str) -> str:
    """定時「今日展望」摘要：每只監控股一行（明日 horizon + 主因）。"""
    lines = [f"📋 今日展望（{slot_label}）", ""]
    for ev in evaluations:
        h = ev["horizons"]["tomorrow"]
        price = f"${ev.get('price'):.2f}" if ev.get("price") else "—"
        lines.append(f"{ev['symbol']}（{price}）：明日 {DIR_ICON[h['direction']]} "
                     f"{h['direction']} {h['confidence']}%")
        lines.append(f"  主因：{h['main_reason'][:60]}")
    lines += ["", "僅供參考，非投資建議"]
    return "\n".join(lines)


def format_compact(evaluation: dict, changes: list[str],
                   critical_event: dict | None = None) -> str:
    sym = evaluation["symbol"]
    lines = [f"🚨 {sym} UPDATE", ""]
    for hz in ("tomorrow", "week", "month", "year"):
        h = evaluation["horizons"][hz]
        lines.append(f"{HZ_LABEL[hz]:4} {DIR_ICON[h['direction']]} {h['direction']} {h['confidence']}%")
    lines.append("")
    for key in ("market", "performance", "serenity", "events", "options"):
        l = evaluation["layers"][key]
        if l["status"] == "unavailable":
            lines.append(f"{LAYER_LABEL[key]:12} ⚪ UNAVAILABLE")
        else:
            lines.append(f"{LAYER_LABEL[key]:12} {_layer_icon(l['score'], l['status'])} {l['score']:+.1f}")
    if critical_event:
        lines += ["", f"⚡ New: {critical_event.get('headline', '')[:90]}",
                  f"Status: {critical_event.get('status_note', '—')}"]
    risk = evaluation["horizons"]["tomorrow"].get("main_risk", "")
    if risk:
        lines += ["", f"Main risk: {risk[:90]}"]
    if changes:
        lines.append("Changed: " + "；".join(c[:60] for c in changes[:4]))
    lines += ["", f"/detail {sym}", "僅供參考，非投資建議"]
    return "\n".join(lines)


def format_critical(symbol: str, evaluation: dict, changes: list[str],
                    critical_event: dict | None = None) -> str:
    lines = [f"🚨🚨 {symbol} CRITICAL SIGNAL", ""]
    for c in changes[:6]:
        lines.append(c)
    if critical_event:
        lines += ["", f"Trigger: {critical_event.get('headline', '')[:100]}",
                  f"Status: {critical_event.get('status_note', '—')}"]
    lines += ["", f"/detail {symbol}", "僅供參考，非投資建議"]
    return "\n".join(lines)
