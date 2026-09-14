"""期權/持倉層（V2 Phase 6）——可選，預設整層 UNAVAILABLE，絕不假裝中性。

OPTIONS_PROVIDER=none（預設）→ unavailable
OPTIONS_PROVIDER=massive → 需 MASSIVE_API_KEY；未配置/失敗 → unavailable

核心紀律（計畫原文）：看空股價 ≠ 自動等於好 put 交易。
IV/權利金極貴時：Underlying bias BEARISH，但 put_attractiveness = LOW。
"""
from datetime import datetime, timezone

from config import MASSIVE_API_KEY, OPTIONS_PROVIDER


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unavailable(reason: str) -> dict:
    return {"status": "unavailable", "score": 0.0, "put_attractiveness": 0,
            "long_attractiveness": 0, "iv_state": None, "reasons": [reason],
            "confidence": 0.0, "updated_at": _now(), "source_count": 0}


def _fetch_massive(symbol: str) -> dict | None:
    """Massive 期權數據抓取。API 規格未接入前回 None（誠實降級，不編造數據）。"""
    if not MASSIVE_API_KEY:
        return None
    # TODO(owner)：接入 Massive options endpoint 後在此實現；
    # 在那之前整層保持 UNAVAILABLE——符合「不假裝中性」鐵律。
    return None


def assess(symbol: str) -> dict:
    if OPTIONS_PROVIDER != "massive":
        return _unavailable("期權層未配置（OPTIONS_PROVIDER=none）")
    raw = _fetch_massive(symbol)
    if raw is None:
        return _unavailable("期權數據源不可用（未接入或憑證缺失）")

    pc_ratio = raw.get("put_call_ratio")
    iv_state = raw.get("iv_state")  # low|normal|elevated|extreme
    if pc_ratio is None or iv_state is None:
        return _unavailable("期權數據不完整")

    # 定價邏輯：put/call 偏空 → 分數偏負；但 IV 極貴時下調做方向的信心並壓低 put 吸引力
    score = max(-10.0, min(10.0, (1.0 - pc_ratio) * 10.0))  # pc>1 偏空
    iv_penalty = {"low": 1.0, "normal": 0.8, "elevated": 0.5, "extreme": 0.3}[iv_state]
    score *= iv_penalty
    put_attractiveness = int(max(0, min(100,
        (-score * 8) if iv_state in ("low", "normal") else -score * 3)))
    long_attractiveness = int(max(0, min(100, score * 8)))
    return {"status": "available", "score": round(score, 2),
            "put_attractiveness": put_attractiveness,
            "long_attractiveness": long_attractiveness,
            "iv_state": iv_state,
            "reasons": [f"P/C {pc_ratio:.2f}；IV {iv_state}"
                        + ("（IV 昂貴：方向偏空 ≠ 好 put 交易）" if iv_penalty < 0.6 else "")],
            "confidence": round(0.3 + 0.2 * iv_penalty, 2),
            "updated_at": _now(), "source_count": 1}
