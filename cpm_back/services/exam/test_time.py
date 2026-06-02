"""Время тестов: окна сдачи и лимит попытки (Москва)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def now_moscow_naive():
    return datetime.now(MOSCOW_TZ).replace(tzinfo=None)


def now_utc_iso():
    return datetime.utcnow().isoformat() + "Z"


def to_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except Exception:
            return None
    return None


def is_test_window_open(test):
    now = now_moscow_naive()
    start_dt = to_datetime(test.get("startDate"))
    end_dt = to_datetime(test.get("endDate"))
    if start_dt is not None and now < start_dt:
        return False, "test_not_started"
    if end_dt is not None and now > end_dt:
        return False, "test_ended"
    return True, None


def remaining_seconds(expires_at_iso):
    """Остаток лимита попытки (startedAt/expiresAt хранятся в UTC)."""
    expires_dt = to_datetime(expires_at_iso)
    if not expires_dt:
        return 0
    delta = (expires_dt - datetime.utcnow()).total_seconds()
    return max(0, int(delta))


def compute_expires_at(started_at_iso, time_limit_minutes):
    started = to_datetime(started_at_iso)
    if not started:
        started = datetime.utcnow()
    limit = int(time_limit_minutes or 0)
    if limit <= 0:
        return started.isoformat() + "Z"
    expires = started + timedelta(minutes=limit)
    iso = expires.isoformat()
    return iso if iso.endswith("Z") else iso + "Z"
