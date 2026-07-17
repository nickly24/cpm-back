"""Время тестов: все бизнес-значения timezone-aware в Europe/Moscow."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def now_moscow():
    return datetime.now(MOSCOW_TZ)


def now_moscow_naive():
    """Legacy helper. Новый код не должен использовать naive datetime."""
    return now_moscow().replace(tzinfo=None)


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def now_moscow_iso():
    return now_moscow().isoformat()


def epoch_ms(value):
    parsed = to_datetime(value)
    return int(parsed.timestamp() * 1000) if parsed else None


def to_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=MOSCOW_TZ)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=MOSCOW_TZ)
        except Exception:
            return None
    return None


def is_test_window_open(test):
    now = now_moscow()
    start_dt = to_datetime(test.get("startDate"))
    end_dt = to_datetime(test.get("endDate"))
    if start_dt:
        start_dt = start_dt.astimezone(MOSCOW_TZ)
    if end_dt:
        end_dt = end_dt.astimezone(MOSCOW_TZ)
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
    delta = (expires_dt.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(delta))


def compute_expires_at(started_at_iso, time_limit_minutes):
    started = to_datetime(started_at_iso)
    if not started:
        started = datetime.now(timezone.utc)
    limit = int(time_limit_minutes or 0)
    if limit <= 0:
        return started.astimezone(MOSCOW_TZ).isoformat()
    expires = started + timedelta(minutes=limit)
    return expires.astimezone(MOSCOW_TZ).isoformat()


def build_attempt_time_fields(started_at, time_limit_minutes, upload_hours=24):
    started = to_datetime(started_at) or now_moscow()
    started = started.astimezone(MOSCOW_TZ)
    deadline = started + timedelta(minutes=max(0, int(time_limit_minutes or 0)))
    upload_deadline = deadline + timedelta(hours=upload_hours)
    server_now = now_moscow()
    return {
        "serverNowMoscow": server_now.isoformat(),
        "serverNowEpochMs": int(server_now.timestamp() * 1000),
        "startedAtMoscow": started.isoformat(),
        "startedAtEpochMs": int(started.timestamp() * 1000),
        "answerDeadlineMoscow": deadline.isoformat(),
        "answerDeadlineEpochMs": int(deadline.timestamp() * 1000),
        "uploadDeadlineMoscow": upload_deadline.isoformat(),
        "uploadDeadlineEpochMs": int(upload_deadline.timestamp() * 1000),
    }
