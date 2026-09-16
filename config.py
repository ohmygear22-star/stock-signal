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
LEARNING_FILE = BASE_DIR / "learning_universe.txt"
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


def load_learning_universe() -> list[dict]:
    """學習宇宙（learning-only）：只採集不推送。格式與 watchlist 相同；
    大寫正規化、忽略空行/註解、跨檔案內部去重。"""
    items, seen = [], set()
    if LEARNING_FILE.exists():
        for raw in LEARNING_FILE.read_text(encoding="utf-8").splitlines():
            entry = parse_watchlist_line(raw)
            if entry and entry["symbol"] not in seen:
                seen.add(entry["symbol"])
                items.append(entry)
    return items


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

# --- AI 提供者（V2：LLM_* 為主，OPENAI_* 為向後兼容的 legacy 鏡像）---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").strip().lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip() or os.getenv("OPENAI_MODEL", "glm-5.3").strip()
LLM_BASE_URL = (os.getenv("LLM_BASE_URL", "").strip() or
                os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
if not LLM_PROVIDER:
    LLM_PROVIDER = "openai_compatible" if LLM_API_KEY else "none"
# legacy 鏡像：V1 代碼仍讀 OPENAI_*，行為不變
OPENAI_API_KEY = LLM_API_KEY if LLM_PROVIDER == "openai_compatible" else ""
OPENAI_MODEL = LLM_MODEL
OPENAI_BASE_URL = LLM_BASE_URL

# 行情 provider（V2）：yfinance 保底；massive 需 MASSIVE_API_KEY，缺證自動回落
MARKET_DATA_PROVIDER = os.getenv("MARKET_DATA_PROVIDER", "yfinance").strip().lower()
MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY", "").strip()

# Serenity provider（V2）：archive 保底；x 需 X_BEARER_TOKEN
SERENITY_PROVIDER = os.getenv("SERENITY_PROVIDER", "archive").strip().lower()
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "").strip()

# 期權 provider（V2）：none = 整層 UNAVAILABLE（不假裝中性）
OPTIONS_PROVIDER = os.getenv("OPTIONS_PROVIDER", "none").strip().lower()

# Telegram 推送（可選；沒設定就只寫 signals.log + 螢幕輸出）
TELEGRAM_PUSH = os.getenv("TELEGRAM_PUSH", "on").strip().lower() != "off"  # off = 靜默採集模式
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# 門檻（%）
INTRADAY_CHANGE_PCT = _float("INTRADAY_CHANGE_PCT", 3.0)
LEV_INTRADAY_CHANGE_PCT = _float("LEV_INTRADAY_CHANGE_PCT", 6.0)
LEV_STOP_PCT = _float("LEV_STOP_PCT", 5.0)
EARNINGS_WARN_DAYS = int(os.getenv("EARNINGS_WARN_DAYS", 5))
