"""美股時段判斷。純本地時鐘運算、零網路請求——launchd 每 15 分鐘喚醒時，
休市可在 1 秒內退出，不對資料源產生任何請求。時區用 America/New_York，
夏令/冬令自動正確，不需要手工調整 cron 時間。"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def status(now: datetime | None = None) -> str:
    """回傳 'open'（盤中）/ 'after_close'（收盤後一小時，出日報）/ 'closed'（其他時間與週末）。"""
    now = now or datetime.now(ET)
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    if time(9, 30) <= t < time(16, 0):
        return "open"
    if time(16, 0) <= t < time(17, 0):
        return "after_close"
    return "closed"


def today_et(now: datetime | None = None) -> str:
    return (now or datetime.now(ET)).strftime("%Y-%m-%d")


def datetime_et(now: datetime | None = None) -> datetime:
    return now or datetime.now(ET)
