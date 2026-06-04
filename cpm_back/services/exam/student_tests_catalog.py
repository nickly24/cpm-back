"""Сборка списков тестов для студента: доступные сейчас и каталог по направлению."""
from datetime import datetime
from zoneinfo import ZoneInfo

from cpm_back.db.mongo import get_mongo_db
from cpm_back.services.exam.create_test_session import (
    get_test_session_by_student_and_test,
    get_test_session_stats,
    get_test_sessions_by_student,
)
from cpm_back.services.exam.get_directions import get_directions
from cpm_back.services.exam.get_external_tests import (
    get_external_tests_with_results_by_student,
)
from cpm_back.services.exam.get_tests_by_direction import get_tests_by_direction
from cpm_back.services.exam.test_attempts import (
    STATUS_EXPIRED,
    normalize_student_id,
    remaining_seconds,
)

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
STUDENT_DEFAULT_LIMIT = 5
STUDENT_MAX_LIMIT = 50

TEST_PROJECTION = {
    "_id": 1,
    "title": 1,
    "direction": 1,
    "startDate": 1,
    "endDate": 1,
    "timeLimitMinutes": 1,
    "visible": 1,
    "published": 1,
}


def now_moscow_naive():
    return datetime.now(MOSCOW_TZ).replace(tzinfo=None)


def now_moscow_iso():
    return datetime.now(MOSCOW_TZ).isoformat()


def parse_dt(value):
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


def clamp_pagination(page, limit):
    limit = min(max(1, int(limit or STUDENT_DEFAULT_LIMIT)), STUDENT_MAX_LIMIT)
    page = max(1, int(page or 1))
    return page, limit


def build_pagination(total_items, page, limit):
    total_pages = (total_items + limit - 1) // limit if total_items else 1
    return {
        "current_page": page,
        "total_pages": total_pages,
        "total_items": total_items,
        "items_per_page": limit,
    }


def paginate_items(items, page, limit):
    total = len(items)
    start = (page - 1) * limit
    end = start + limit
    return items[start:end], build_pagination(total, page, limit)


def _summary_from_attempt_doc(doc):
    answers = doc.get("answers") or []
    order = doc.get("questionOrder") or []
    total_q = len(order)
    answered = len(answers)
    if doc.get("status") == STATUS_EXPIRED:
        return {
            "id": str(doc["_id"]),
            "expiresAt": doc.get("expiresAt"),
            "remainingSeconds": 0,
            "answeredCount": answered,
            "totalQuestions": total_q,
            "expired": True,
        }
    expires_at = doc.get("expiresAt")
    rem = remaining_seconds(expires_at) if expires_at else 0
    return {
        "id": str(doc["_id"]),
        "expiresAt": expires_at,
        "remainingSeconds": rem,
        "answeredCount": answered,
        "totalQuestions": total_q,
        "expired": False,
    }


def _load_pending_map(student_id, session_test_ids):
    """Активные попытки студента: testId -> summary (один запрос в Mongo)."""
    sid = normalize_student_id(student_id)
    if sid is None:
        return {}
    coll = get_mongo_db().test_attempts
    result = {}
    cursor = coll.find({
        "studentId": sid,
        "status": {"$in": ["in_progress", STATUS_EXPIRED]},
        "isPractice": False,
    })
    for doc in cursor:
        test_id = str(doc.get("testId") or "")
        if not test_id:
            continue
        if test_id in session_test_ids:
            continue
        result[test_id] = _summary_from_attempt_doc(doc)
    return result


def _serialize_internal_test(doc):
    return {
        "id": str(doc["_id"]),
        "title": doc.get("title"),
        "startDate": doc.get("startDate"),
        "endDate": doc.get("endDate"),
        "timeLimitMinutes": doc.get("timeLimitMinutes"),
        "visible": doc.get("visible", False),
        "published": doc.get("published", True),
        "direction": doc.get("direction"),
    }


def get_all_published_tests_light():
    db = get_mongo_db()
    cursor = db.tests.find({}, TEST_PROJECTION).sort("startDate", -1)
    items = []
    for doc in cursor:
        if doc.get("published") is False:
            continue
        items.append(_serialize_internal_test(doc))
    return items


def enrich_test_item(test_item, completed_ids, student_id, pending_map):
    is_external = bool(test_item.get("isExternal") or test_item.get("externalTest"))
    now = now_moscow_naive()
    start_dt = parse_dt(test_item.get("startDate"))
    end_dt = parse_dt(test_item.get("endDate"))
    is_completed = str(test_item.get("id")) in completed_ids
    is_upcoming = (not is_external) and start_dt is not None and now < start_dt
    is_active = (
        (not is_external)
        and start_dt is not None
        and end_dt is not None
        and (start_dt <= now <= end_dt)
    )
    is_missed = (not is_external) and end_dt is not None and now > end_dt and not is_completed
    can_start = (not is_external) and is_active and (not is_completed)
    can_practice = (not is_external) and is_completed
    can_view_results = is_completed and bool(test_item.get("visible"))
    status = "external"
    if not is_external:
        if is_completed:
            status = "completed"
        elif is_active:
            status = "available"
        elif is_upcoming:
            status = "upcoming"
        else:
            status = "missed"

    enriched = dict(test_item)
    enriched.update({
        "isCompleted": is_completed,
        "isUpcoming": is_upcoming,
        "isActive": is_active,
        "isMissed": is_missed,
        "status": status,
        "canStart": can_start,
        "canPractice": can_practice,
        "canViewResults": can_view_results,
        "isExternal": is_external,
        "canResume": False,
        "canSubmitExpired": False,
    })

    if not is_external and student_id:
        pending = pending_map.get(str(test_item.get("id")))
        if pending:
            enriched["activeAttempt"] = pending
            enriched["canSubmitExpired"] = bool(pending.get("expired"))
            enriched["canResume"] = (
                not pending.get("expired")
                and pending.get("remainingSeconds", 0) > 0
            )
            if enriched.get("canResume") or enriched.get("canSubmitExpired"):
                enriched["canStart"] = False

    return enriched


def is_actionable_test(item):
    return bool(
        item.get("canStart")
        or item.get("canResume")
        or item.get("canSubmitExpired")
    )


def _actionable_sort_key(item):
    if item.get("canSubmitExpired"):
        rank = 0
    elif item.get("canResume"):
        rank = 1
    elif item.get("canStart"):
        rank = 2
    else:
        rank = 3
    end_dt = parse_dt(item.get("endDate")) or datetime.max
    return (rank, end_dt)


def _load_student_context(student_id):
    sessions = get_test_sessions_by_student(student_id)
    completed_ids = {str(s.get("testId")) for s in sessions if s.get("testId")}
    sessions_by_test = {str(s.get("testId")): s for s in sessions if s.get("testId")}
    pending_map = _load_pending_map(student_id, set(sessions_by_test.keys()))
    return sessions, completed_ids, sessions_by_test, pending_map


def _sessions_for_tests(tests, sessions_by_test, with_stats=False):
    page_sessions = []
    for test in tests:
        test_id = str(test.get("id"))
        session = sessions_by_test.get(test_id)
        if not session:
            continue
        entry = dict(session)
        if with_stats and entry.get("id"):
            entry["stats"] = get_test_session_stats(entry["id"])
        page_sessions.append(entry)
    return page_sessions


def build_available_tests_response(student_id, page=1, limit=STUDENT_DEFAULT_LIMIT):
    page, limit = clamp_pagination(page, limit)
    sessions, completed_ids, sessions_by_test, pending_map = _load_student_context(student_id)

    internal = get_all_published_tests_light()
    enriched = [
        enrich_test_item(t, completed_ids, student_id, pending_map)
        for t in internal
    ]
    actionable = [t for t in enriched if is_actionable_test(t)]
    actionable.sort(key=_actionable_sort_key)

    page_tests, pagination = paginate_items(actionable, page, limit)
    for item in page_tests:
        item["directionName"] = item.get("direction")

    page_sessions = _sessions_for_tests(page_tests, sessions_by_test, with_stats=False)

    return {
        "success": True,
        "tests": page_tests,
        "sessions": page_sessions,
        "pagination": pagination,
        "serverTimeMoscow": now_moscow_iso(),
        "totalActionable": len(actionable),
    }


def build_direction_tests_response(student_id, direction_name, page=1, limit=STUDENT_DEFAULT_LIMIT):
    page, limit = clamp_pagination(page, limit)
    sessions, completed_ids, sessions_by_test, pending_map = _load_student_context(student_id)

    internal_tests = get_tests_by_direction(direction_name)
    internal_tests = [t for t in internal_tests if t.get("published", True)]

    directions = get_directions()
    direction_obj = next((d for d in directions if d.get("name") == direction_name), None)
    external_tests = []

    if direction_obj:
        direction_id = direction_obj.get("id")
        try:
            student_id_int = int(student_id) if student_id else None
            external_tests = get_external_tests_with_results_by_student(
                direction_id, student_id_int
            )
            for t in external_tests:
                if t.get("hasResult") and t.get("id"):
                    completed_ids.add(str(t.get("id")))
        except Exception:
            pass

    combined = []
    for raw in (internal_tests + external_tests):
        item = enrich_test_item(raw, completed_ids, student_id, pending_map)
        item["directionName"] = direction_name
        combined.append(item)

    page_tests, pagination = paginate_items(combined, page, limit)
    page_sessions = _sessions_for_tests(page_tests, sessions_by_test, with_stats=True)

    counts = {
        "all": len(combined),
        "available": sum(1 for t in combined if t.get("status") == "available"),
        "upcoming": sum(1 for t in combined if t.get("status") == "upcoming"),
        "completed": sum(1 for t in combined if t.get("status") == "completed"),
        "missed": sum(1 for t in combined if t.get("status") == "missed"),
        "external": sum(1 for t in combined if t.get("status") == "external"),
    }

    return {
        "success": True,
        "tests": page_tests,
        "sessions": page_sessions,
        "pagination": pagination,
        "counts": counts,
        "serverTimeMoscow": now_moscow_iso(),
    }
