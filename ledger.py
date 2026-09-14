"""信號賬本：每個警報進賬、事後自動對答案、每週記分卡。

- 記錄：signals_ledger.jsonl，每行一個警報事件
- 事件語義：同一 (symbol, rule_id) 連續觸發只記一次（episode 入場制），
  活躍狀態存 ledger_state.json
- 打分：score_pending() 對滿 3 個交易日的事件結算 +3 日方向化收益
- 記分卡：scorecard() 按 rule_id 聚合近期命中率
Serenity 提股、絆網警報也寫進同一本賬（rule_id 前綴區分）。"""
import json
from datetime import date, datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
LEDGER_FILE = BASE / "signals_ledger.jsonl"
STATE_FILE = BASE / "ledger_state.json"
SCORE_HORIZON = 3  # 個交易日


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_all() -> list[dict]:
    if not LEDGER_FILE.exists():
        return []
    return [json.loads(line) for line in LEDGER_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_all(records: list[dict]) -> None:
    with LEDGER_FILE.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def update_active(symbol: str, fired_now: list[dict], price: float, event_day: bool = False) -> list[dict]:
    """episode 入場制記錄：回傳「這一輪新進場」的事件（值得推送/記賬）。
    fired_now = 當前掃描中該 symbol 的活躍信號列表（含 rule_id）。"""
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    key_prefix = f"{symbol}|"
    active_keys = {k for k in state if k.startswith(key_prefix)}
    now_keys = {f"{symbol}|{s['rule_id']}" for s in fired_now}

    fresh = []
    for s in fired_now:
        k = f"{symbol}|{s['rule_id']}"
        if k not in active_keys:
            fresh.append(s)
            state[k] = _now_iso()
    for k in active_keys - now_keys:  # episode 結束，移出活躍集
        state.pop(k)
    STATE_FILE.write_text(json.dumps(state))

    for s in fresh:
        _append({
            "ts": _now_iso(),
            "symbol": symbol,
            "rule_id": s["rule_id"],
            "level": s["level"],
            "dir": s["dir"],
            "price": price,
            "event_day": event_day,
            "scored": False,
        })
    return fresh


def record_event(symbol: str, rule_id: str, level: str, direction: str, price: float,
                 extra: dict | None = None, dedup_key: str | None = None) -> None:
    """一次性事件（Serenity 提股、絆網等）直接記賬；dedup_key 相同且 24h 內出現過則跳過。"""
    records = _read_all()
    if dedup_key:
        cutoff = datetime.now(timezone.utc).timestamp() - 86400
        for r in records:
            if r.get("dedup_key") == dedup_key:
                ts = datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if ts.timestamp() > cutoff:
                    return
    row = {
        "ts": _now_iso(),
        "symbol": symbol,
        "rule_id": rule_id,
        "level": level,
        "dir": direction,
        "price": price,
        "event_day": False,
        "scored": False,
    }
    if extra:
        row.update(extra)
    if dedup_key:
        row["dedup_key"] = dedup_key
    _append(row)


def score_pending(price_history_fn) -> int:
    """結算滿 3 個交易日的事件。price_history_fn(symbol) → daily DataFrame。
    回傳結算條數。"""
    records = _read_all()
    changed = 0
    hist_cache = {}
    for r in records:
        if r["scored"]:
            continue
        d = datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ").date()
        if (date.today() - d).days < SCORE_HORIZON + 2:  # 緩衝，保證有 3 個交易日
            continue
        sym = r["symbol"]
        if sym not in hist_cache:
            try:
                hist_cache[sym] = price_history_fn(sym)
            except Exception:
                hist_cache[sym] = None
        df = hist_cache[sym]
        if df is None or df.empty:
            continue
        idx = [i for i, ts in enumerate(df.index) if ts.date() >= d]
        if len(idx) < SCORE_HORIZON + 1:
            continue
        i0 = idx[0]
        entry = float(df["Close"].iloc[i0]) if abs(float(df["Close"].iloc[i0]) - r["price"]) / max(r["price"], 1e-9) < 0.05 else r["price"]
        fwd = float(df["Close"].iloc[i0 + SCORE_HORIZON]) / entry - 1
        sign = 1 if r["dir"] in ("多", "LONG") else -1
        r["scored"] = True
        r["fwd_ret_3d"] = round(fwd * sign, 4)
        r["hit"] = (fwd * sign) > 0
        changed += 1
    if changed:
        _write_all(records)
    return changed


def scorecard(days: int = 7) -> str:
    """近 N 天記分卡（中文，供推送）。"""
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    records = [r for r in _read_all()
               if datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() >= cutoff]
    if not records:
        return f"📊 記分卡（近 {days} 天）：暫無警報記錄"
    by_rule = {}
    for r in records:
        by_rule.setdefault(r["rule_id"], {"n": 0, "hit": 0, "scored": 0, "ret": []})
        b = by_rule[r["rule_id"]]
        b["n"] += 1
        if r.get("scored"):
            b["scored"] += 1
            b["hit"] += int(bool(r.get("hit")))
            b["ret"].append(r.get("fwd_ret_3d", 0))
    lines = [f"📊 記分卡（近 {days} 天，警報 {len(records)} 次）", ""]
    for rid, b in sorted(by_rule.items(), key=lambda kv: -kv[1]["n"]):
        scored_part = (f"已結算 {b['scored']} 次，命中率 {b['hit']}/{b['scored']}"
                       f"（平均 {sum(b['ret']) / len(b['ret']):+.2%}）") if b["scored"] else "尚未結算"
        lines.append(f"• {rid}：觸發 {b['n']} 次；{scored_part}")
    lines.append("")
    lines.append("口徑：結算 = 警報後 3 個交易日方向化收益；僅供參考，非投資建議")
    return "\n".join(lines)


def _append(row: dict) -> None:
    with LEDGER_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def system_status() -> str:
    records = _read_all()
    scored = sum(1 for r in records if r.get("scored"))
    return f"賬本 {len(records)} 條（已結算 {scored}）"
