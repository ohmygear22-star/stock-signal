"""AI 提供者抽象層（V2 Phase 1）。

唯一穩定接口：complete(system, user, temperature) -> str | None
- LLM_PROVIDER=none               → 永遠回 None，其他子系統不得受影響
- LLM_PROVIDER=openai_compatible   → 走 LLM_API_KEY / LLM_MODEL / LLM_BASE_URL
向後兼容：未設 LLM_* 時沿用舊 OPENAI_* 變數（經 config 正規化）。
"""
import requests

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER


def available() -> bool:
    return LLM_PROVIDER == "openai_compatible" and bool(LLM_API_KEY)


def model_name() -> str:
    return LLM_MODEL


def complete(system: str, user: str, temperature: float = 0.2) -> str | None:
    if not available():
        return None
    try:
        resp = requests.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return None
