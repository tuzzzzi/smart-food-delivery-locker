from datetime import datetime, timezone
from typing import Optional

from zoneinfo import ZoneInfo


PROJECT_TIMEZONE_NAME = "Asia/Shanghai"
PROJECT_TIMEZONE = ZoneInfo(PROJECT_TIMEZONE_NAME)


def to_project_timezone(dt: Optional[datetime]) -> Optional[datetime]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PROJECT_TIMEZONE)


def project_now() -> datetime:
    return datetime.now(PROJECT_TIMEZONE)


def project_date_key(dt: Optional[datetime] = None) -> str:
    localized = to_project_timezone(dt) if dt else project_now()
    return localized.strftime("%Y-%m-%d")
