"""
app/services/timezone_utils.py  –  統一的時區管理工具
確保整個應用使用台北時區（UTC+8）
"""
from datetime import datetime, timezone, timedelta, date as dt_date, time as dt_time
from typing import Tuple

# 台北時區：UTC+8
TAIPEI_TZ = timezone(timedelta(hours=8))


def get_taipei_now() -> datetime:
    """
    取得台北時區的當前時間
    Returns: datetime with timezone info (UTC+8)
    """
    return datetime.now(TAIPEI_TZ)


def get_taipei_now_no_tz() -> datetime:
    """
    取得台北時區的當前時間（無時區資訊）
    Returns: naive datetime
    """
    return datetime.now(TAIPEI_TZ).replace(tzinfo=None)


def get_taipei_date() -> dt_date:
    """取得台北時區的當前日期"""
    return get_taipei_now_no_tz().date()


def get_taipei_time() -> dt_time:
    """取得台北時區的當前時刻"""
    return get_taipei_now_no_tz().time()


def get_taipei_date_and_time() -> Tuple[dt_date, dt_time]:
    """
    取得台北時區的當前日期和時刻
    Returns: (date, time) tuple
    """
    now = get_taipei_now_no_tz()
    return now.date(), now.time()


def format_time(t: dt_time) -> str:
    """
    格式化時間為 HH:MM:SS
    Args: datetime.time object
    Returns: formatted string "HH:MM:SS"
    """
    if isinstance(t, dt_time):
        return t.strftime("%H:%M:%S")
    return str(t)


def format_datetime(dt: datetime) -> str:
    """
    格式化日期時間為 YYYY-MM-DD HH:MM:SS
    Args: datetime object
    Returns: formatted string "YYYY-MM-DD HH:MM:SS"
    """
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    return str(dt)


def format_date(d: dt_date) -> str:
    """
    格式化日期為 YYYY-MM-DD
    Args: datetime.date object
    Returns: formatted string "YYYY-MM-DD"
    """
    if isinstance(d, dt_date):
        return d.strftime("%Y-%m-%d")
    return str(d)


def is_time_expired(slot_date: dt_date, end_time: dt_time) -> bool:
    """
    檢查面試時間是否已經過期
    Args:
        slot_date: 面試日期
        end_time: 面試結束時間
    Returns: True if expired, False otherwise
    """
    current_date, current_time = get_taipei_date_and_time()
    
    # 過期條件：(日期在過去) 或 (日期相同但時間已過)
    return (slot_date < current_date) or (slot_date == current_date and end_time <= current_time)


def is_slot_in_future(slot_date: dt_date, start_time: dt_time) -> bool:
    """
    檢查面試時段是否在未來
    Args:
        slot_date: 面試日期
        start_time: 面試開始時間
    Returns: True if in future, False otherwise
    """
    current_date, current_time = get_taipei_date_and_time()
    
    # 未來條件：(日期在未來) 或 (日期相同但時間未到)
    return (slot_date > current_date) or (slot_date == current_date and start_time >= current_time)


# 示例用法
if __name__ == "__main__":
    print("="*80)
    print("台北時區時間工具示例")
    print("="*80)
    print(f"當前台北時間: {format_datetime(get_taipei_now())}")
    print(f"當前台北日期: {format_date(get_taipei_date())}")
    print(f"當前台北時刻: {format_time(get_taipei_time())}")
    
    # 測試時間比較
    from datetime import date, time
    test_date = date(2026, 5, 28)
    test_time = time(12, 30)
    print(f"\n測試: 2026-05-28 12:30:00 是否已過期? {is_time_expired(test_date, test_time)}")
