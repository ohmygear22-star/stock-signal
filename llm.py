"""消息面分析層（V2 起全部經 ai_provider 路由，本檔不再直接持有 HTTP 邏輯）。
沒有可用 LLM 時所有函數安靜返回 None / 降級——機械信號層永不受影響。"""
import json
import re

import ai_provider
from config import OPENAI_API_KEY  # V1 兼容鏡像（其他模組用 llm.OPENAI_API_KEY 判斷可用性）


def model_label() -> str:
    return ai_provider.model_name()


def complete(system: str, user: str, temperature: float = 0.2) -> str | None:
    """通用 LLM 調用（宏觀面分類、新聞快評、翻譯等）。"""
    return ai_provider.complete(system, user, temperature)


def translate_tc(text: str) -> str | None:
    """推送用翻譯：譯成繁體中文，保留 $ticker/數字/專名，零添加觀點。失敗回 None（調用方回退原文）。"""
    return complete(
        "你是翻譯引擎。把給定內容翻譯成繁體中文（台灣用語），"
        "$ticker 代碼、數字、公司與人名保留原文，不加任何解釋、觀點或emoji，只輸出譯文。",
        text,
    )


def translate_tc_batch(texts: list[str]) -> list[str | None]:
    """批量翻譯（一次調用）：逐行對應返回譯文，失敗位置為 None。"""
    if not texts or not ai_provider.available():
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


SYSTEM_PROMPT = """你是嚴謹的股票消息面分析師。用戶會給你技術指標摘要和新聞標題，你只做消息面判讀。
規則：
1. 只根據給定資訊判斷，不編造不存在的新聞或數據。
2. 用戶訊息會說明監控標的與底層股票的方向關係（正相關或反向），評分必須按該關係換算到「監控標的本身」。
3. 必須只輸出一個 JSON 對象，不要輸出其他文字，格式：
{"score": <-2 到 2 的整數，正=利多監控標的本身>, "stance": "<偏多|中性|偏空>", "reasons": ["原因1", "原因2", "原因3"]}
4. 全部用繁體中文。新聞不足以下判斷時 score 給 0，reasons 說明資訊不足。"""


def analyze(target_symbol: str, underlying: str, summary: dict, news: list[dict],
            inverse: bool = False) -> dict | None:
    """回傳 {'score', 'stance', 'reasons'}；未設定 key 或呼叫失敗回傳 None。"""
    if not ai_provider.available():
        return None
    relation = (f"{underlying} 利多 = {target_symbol} 利空，方向需反轉" if inverse
                else f"{target_symbol} 與 {underlying} 同方向")
    headlines = "\n".join(f"- [{n['publisher']}] {n['title']}" for n in news) or "（近來無新聞）"
    user_msg = (
        f"監控標的：{target_symbol}（方向關係：{relation}）\n"
        f"技術指標摘要：{json.dumps(summary, ensure_ascii=False)}\n\n"
        f"{underlying} 近期新聞標題：\n{headlines}"
    )
    content = complete(SYSTEM_PROMPT, user_msg)
    if content is None:
        return {"score": 0, "stance": "分析失敗", "reasons": ["LLM 呼叫失敗"]}
    return _parse_json(content)


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
