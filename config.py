"""設定載入。可調參數與敏感資訊的規則：
- 監控清單 → watchlist.txt（純文字，任何人都能編輯，不依賴 .env）
- API key → 只放 .env（絕不貼進對話）
本工具的機械信號層不需要任何 AI 服務也能完整運行。"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
WATCHLIST_FILE = BASE_DIR / "watchlist.txt"
DAILY_STATE_FILE = BASE_DIR / ".last_daily"

VALID_PROFILES = {"stock", "leveraged_inverse"}


def parse_watchlist_line(line: str) -> dict | None:
    """解析單行監控條目（bot 的 /add 與 watchlist.txt 共用）。無效行回 None。
    自動把全角字符（ｃｒｗｖ、：）正規化為半角，中文輸入法友善。"""
    import unicodedata
    line = unicodedata.normalize("NFKC", line.split("#", 1)[0]).strip()
    if not line:
        return None
    parts = [p.strip() for p in line.split(":")]
    symbol = parts[0].upper()
    profile = parts[1].lower() if len(parts) > 1 and parts[1] else "stock"
    if profile not in VALID_PROFILES:
        profile = "stock"
    underlying = parts[2].upper() if len(parts) > 2 and parts[2] else symbol
    return {"symbol": symbol, "profile": profile, "underlying": underlying}


def load_watchlist() -> list[dict]:
    """解析 watchlist.txt（每行格式見 parse_watchlist_line）；
    檔案不存在或為空時退回 .env 的 WATCHLIST，再不行就空清單。"""
    items = []
    if WATCHLIST_FILE.exists():
        for raw in WATCHLIST_FILE.read_text(encoding="utf-8").splitlines():
            entry = parse_watchlist_line(raw)
            if entry:
                items.append(entry)
    if not items:
        for s in os.getenv("WATCHLIST", "").split(","):
            if s.strip():
                items.append({"symbol": s.strip().upper(), "profile": "stock", "underlying": s.strip().upper()})
    return items


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except ValueError:
        return default

# OpenAI（模型名稱未經聯網核實，預設 astra；不填 key 就自動跳過消息面層，其餘功能完整）
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "astra")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

# Telegram 推送（可選；沒設定就只寫 signals.log + 螢幕輸出）
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# 門檻（%）
INTRADAY_CHANGE_PCT = _float("INTRADAY_CHANGE_PCT", 3.0)
LEV_INTRADAY_CHANGE_PCT = _float("LEV_INTRADAY_CHANGE_PCT", 6.0)
LEV_STOP_PCT = _float("LEV_STOP_PCT", 5.0)
EARNINGS_WARN_DAYS = int(os.getenv("EARNINGS_WARN_DAYS", 5))
