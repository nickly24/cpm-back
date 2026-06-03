"""Админ: просмотр и удаление test_sessions и test_attempts."""
from cpm_back.services.exam.admin_list_utils import (
    build_pagination,
    normalize_search_query,
    parse_page_limit,
)
from cpm_back.services.exam.create_test import get_test_by_id
from cpm_back.services.exam.create_test_session import (
    aggregate_test_sessions_stats,
    count_test_sessions_by_test,
    delete_test_session_by_id,
    get_test_session_by_id,
    get_test_session_stats,
    list_test_sessions_by_test_paginated,
)
from cpm_back.services.exam.student_names import (
    get_student_names_by_ids,
    resolve_student_name,
    search_student_ids_by_name,
)
from cpm_back.services.exam.test_attempts import (
    STATUS_EXPIRED,
    STATUS_IN_PROGRESS,
    STATUS_SUBMITTED,
    count_attempts_by_test,
    count_attempts_by_test_and_status,
    delete_attempt_by_id,
    get_attempt_admin_detail,
    list_attempts_by_test_paginated,
)

__all__ = [
    "list_test_sessions_admin",
    "get_test_session_admin_detail",
    "delete_test_session_admin",
    "list_test_attempts_admin",
    "get_test_attempt_admin_detail",
    "delete_test_attempt_admin",
    "get_test_admin_overview",
]


def _resolve_search_student_ids(search):
    q = normalize_search_query(search)
    if not q:
        return None, ""
    if len(q) < 2:
        return None, q
    ids = search_student_ids_by_name(q)
    return ids if ids is not None else [], q


def _empty_list_response(test_id, page, limit, search_query, items_key):
    return {
        "success": True,
        "testId": str(test_id),
        items_key: [],
        "pagination": build_pagination(0, page, limit),
        "search": search_query,
    }


def _enrich_sessions(sessions):
    names = get_student_names_by_ids([s.get("studentId") for s in sessions])
    items = []
    for s in sessions:
        sid = s.get("studentId")
        items.append({
            "sessionId": s.get("id"),
            "studentId": sid,
            "studentFullName": resolve_student_name(sid, names) or f"Студент #{sid}",
            "testTitle": s.get("testTitle"),
            "score": s.get("score"),
            "completedAt": s.get("completedAt"),
            "timeSpentMinutes": s.get("timeSpentMinutes"),
            "answersCount": s.get("answersCount"),
        })
    return items


def list_test_sessions_admin(test_id, page=1, limit=10, search=None):
    page, limit, skip = parse_page_limit(page, limit)
    student_ids, search_query = _resolve_search_student_ids(search)

    if student_ids is not None and len(student_ids) == 0:
        return _empty_list_response(test_id, page, limit, search_query, "sessions")

    total = count_test_sessions_by_test(test_id, student_ids=student_ids)
    sessions = list_test_sessions_by_test_paginated(
        test_id, skip=skip, limit=limit, student_ids=student_ids,
    )

    return {
        "success": True,
        "testId": str(test_id),
        "sessions": _enrich_sessions(sessions),
        "pagination": build_pagination(total, page, limit),
        "search": search_query,
    }


def get_test_session_admin_detail(session_id):
    session = get_test_session_by_id(session_id)
    if not session:
        return {"success": False, "error": "session_not_found"}

    stats = get_test_session_stats(session_id)
    names = get_student_names_by_ids([session.get("studentId")])
    sid = session.get("studentId")

    return {
        "success": True,
        "session": session,
        "studentFullName": resolve_student_name(sid, names) or f"Студент #{sid}",
        "stats": stats,
    }


def delete_test_session_admin(session_id):
    session = get_test_session_by_id(session_id)
    if not session:
        return {"success": False, "error": "session_not_found"}
    deleted = delete_test_session_by_id(session_id)
    if not deleted:
        return {"success": False, "error": "delete_failed"}
    return {
        "success": True,
        "sessionId": str(session_id),
        "message": "Сессия удалена. Студент сможет сдать тест заново.",
    }


def list_test_attempts_admin(test_id, status_filter=None, page=1, limit=10, search=None):
    test = get_test_by_id(test_id)
    if not test:
        return {"success": False, "error": "test_not_found"}

    page, limit, skip = parse_page_limit(page, limit)
    student_ids, search_query = _resolve_search_student_ids(search)

    if student_ids is not None and len(student_ids) == 0:
        result = _empty_list_response(test_id, page, limit, search_query, "attempts")
        result["testTitle"] = test.get("title")
        return result

    total = count_attempts_by_test(
        test_id, status_filter=status_filter, student_ids=student_ids,
    )
    attempts = list_attempts_by_test_paginated(
        test_id,
        skip=skip,
        limit=limit,
        status_filter=status_filter,
        student_ids=student_ids,
    )
    names = get_student_names_by_ids([a.get("studentId") for a in attempts])
    items = []
    for a in attempts:
        sid = a.get("studentId")
        items.append({
            **a,
            "studentFullName": resolve_student_name(sid, names) or f"Студент #{sid}",
        })

    return {
        "success": True,
        "testId": str(test_id),
        "testTitle": test.get("title"),
        "attempts": items,
        "pagination": build_pagination(total, page, limit),
        "search": search_query,
    }


def get_test_admin_overview(test_id):
    """Лёгкая сводка по тесту (отдельный запрос, без списков)."""
    test = get_test_by_id(test_id)
    if not test:
        return {"success": False, "error": "test_not_found"}

    session_stats = aggregate_test_sessions_stats(test_id)
    in_progress = count_attempts_by_test_and_status(test_id, [STATUS_IN_PROGRESS])
    expired = count_attempts_by_test_and_status(test_id, [STATUS_EXPIRED])
    submitted = count_attempts_by_test_and_status(test_id, [STATUS_SUBMITTED])

    return {
        "success": True,
        "testId": str(test_id),
        "testTitle": test.get("title"),
        "analytics": {
            "sessionsCompleted": session_stats["count"],
            "averageScore": session_stats["averageScore"],
            "attemptsInProgress": in_progress,
            "attemptsExpired": expired,
            "attemptsSubmitted": submitted,
            "attemptsActive": in_progress + expired,
        },
    }


def get_test_attempt_admin_detail(attempt_id):
    return get_attempt_admin_detail(attempt_id)


def delete_test_attempt_admin(attempt_id):
    attempt = get_attempt_admin_detail(attempt_id, brief=True)
    if not attempt.get("success"):
        return attempt

    deleted = delete_attempt_by_id(attempt_id)
    if not deleted:
        return {"success": False, "error": "delete_failed"}

    return {
        "success": True,
        "attemptId": str(attempt_id),
        "message": "Попытка удалена. Студент может начать тест заново (если нет финальной сессии).",
    }
