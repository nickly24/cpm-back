"""Единые правила доступа студента к разбору и тренировке."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from cpm_back.services.exam.test_time import MOSCOW_TZ, now_moscow, to_datetime


@dataclass(frozen=True)
class StudentTestAccess:
    can_practice: bool
    can_view_results: bool
    official_window_ended: bool
    practice_error: Optional[str] = None


def resolve_student_test_access(
    test,
    *,
    has_completed_session: bool,
    has_open_official_attempt: bool = False,
    is_external: bool = False,
    current_time: Optional[datetime] = None,
) -> StudentTestAccess:
    """`visible` is the single gate for both review and practice."""
    if is_external:
        return StudentTestAccess(False, False, False, "external_test_not_supported")

    visible = bool((test or {}).get("visible"))
    end_at = to_datetime((test or {}).get("endDate"))
    now = (current_time or now_moscow()).astimezone(MOSCOW_TZ)
    official_window_ended = bool(
        end_at and now > end_at.astimezone(MOSCOW_TZ)
    )

    can_view_results = visible and bool(has_completed_session)
    can_practice = visible and bool(
        has_completed_session
        or (official_window_ended and not has_open_official_attempt)
    )

    if can_practice:
        practice_error = None
    elif not visible:
        practice_error = "practice_not_published"
    elif has_open_official_attempt:
        practice_error = "official_attempt_pending"
    else:
        practice_error = "practice_before_official_completion"

    return StudentTestAccess(
        can_practice=can_practice,
        can_view_results=can_view_results,
        official_window_ended=official_window_ended,
        practice_error=practice_error,
    )
