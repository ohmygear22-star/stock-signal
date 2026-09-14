"""消息面分析層：把指標摘要 + 新聞標題交給 OpenAI（Astra）評分。沒有 API key 時自動跳過。
注意：LLM 的輸出是「參考意見」，不是買賣信號本體；信號一律以規則引擎為準。"""
import json
import re

import requests

from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

SYSTEM_PROMPT = """你是嚴謹的股票消息面分析師。用戶會給你技術指標摘要和新聞標題，你只做消息面判讀。
規則：
1. 只根據給定資訊判斷，不編造不存在的新聞或數據。
2. 用戶訊息會說明監控標的與底層股票的方向關係（正相關或反向），評分必須按該關係換算到「監控標的本身」。
3. 必須只輸出一個 JSON 對象，不要輸出其他文字，格式：
{"score": <-2 到 2 的整數，正=利多監控標的本身>, "stance": "<偏多|中性|偏空>", "reasons": ["原因1", "原因2", "原因3"]}
4. 全部用繁體中文。新聞不足以下判斷時 score 給 0，reasons 說明資訊不足。"""


def complete(system: str, user: str, temperature: float = 0.2) -> str | None:
    """通用 LLM 調用（宏觀面分類、新聞快評等用）。未設定 key 回傳 None。"""
    if not OPENAI_API_KEY:
        return None
    try:
        resp = requests.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": OPENAI_MODEL,
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


def translate_tc(text: str) -> str | None:
    """推送用翻譯：譯成繁體中文，保留 $ticker/數字/專名，零添加觀點。失敗回 None（調用方回退原文）。"""
    return complete(
        "你是翻譯引擎。把給定內容翻譯成繁體中文（台灣用語），"
        "$ticker 代碼、數字、公司與人名保留原文，不加任何解釋、觀點或emoji，只輸出譯文。",
        text,
    )


def translate_tc_batch(texts: list[str]) -> list[str | None]:
    """批量翻譯（一次調用）：逐行對應返回譯文，失敗位置為 None。"""
    if not texts or not OPENAI_API_KEY:
        return [None] * len(texts)
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    out = complete(
        "你是翻譯引擎。把每條編號標題翻譯成繁體中文（台灣用語），$ticker/數字/專名保留原文。"
        "逐行輸出「編號. 譯文」，不加其他文字。",
        numbered,
    )
    if not out:
        return [None] * len(texts)
    lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
    result: list[str | None] = [None] * len(texts)
    for line in lines:
        head, _, rest = line.partition(".")
        if head.strip().isdigit() and 1 <= int(head) <= len(texts):
            result[int(head) - 1] = rest.strip() or None
    return result


def analyze(target_symbol: str, underlying: str, summary: dict, news: list[dict],
            inverse: bool = False) -> dict | None:
    """回傳 {'score', 'stance', 'reasons'}；未設定 key 或呼叫失敗回傳 None。"""
    if not OPENAI_API_KEY:
        return None
    relation = (f"{underlying} 利多 = {target_symbol} 利空，方向需反轉" if inverse
                else f"{target_symbol} 與 {underlying} 同方向")
    headlines = "\n".join(f"- [{n['publisher']}] {n['title']}" for n in news) or "（近來無新聞）"
    user_msg = (
        f"監控標的：{target_symbol}（方向關係：{relation}）\n"
        f"技術指標摘要：{json.dumps(summary, ensure_ascii=False)}\n\n"
        f"{underlying} 近期新聞標題：\n{headlines}"
    )
    try:
        resp = requests.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": OPENAI_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.2,
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return _parse_json(content)
    except Exception as exc:
        return {"score": 0, "stance": "分析失敗", "reasons": [f"LLM 呼叫失敗：{exc}"]}


def _parse_json(content: str) -> dict | None:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        if {"score", "stance", "reasons"} <= obj.keys():
            return obj
    except json.JSONDecodeError:
        pass
    return None
