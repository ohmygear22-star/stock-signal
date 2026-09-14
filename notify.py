"""通知層：有 Telegram 就推送，沒有就寫 signals.log + 螢幕輸出。"""
from datetime import datetime
from pathlib import Path

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

LOG_PATH = Path(__file__).parent / "signals.log"


def send(title: str, body: str) -> None:
    text = f"{title}\n{body}"
    print(text)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}\n")
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
                timeout=15,
            )
        except Exception as exc:
            print(f"（Telegram 推送失敗：{exc}，信號仍已寫入 {LOG_PATH.name}）")
