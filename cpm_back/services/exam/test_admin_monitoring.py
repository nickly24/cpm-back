"""Админ: просмотр и удаление test_sessions и test_attempts."""
from cpm_back.services.exam.create_test import get_test_by_id
from cpm_back.services.exam.create_test_session import (
    delete_test_session_by_id,
    get_test_session_by_id,
    get_test_session_stats,
    get_test_sessions_by_test,
)
from cpm_back.services.exam.student_names import get_student_names_by_ids, resolve_student_name
from cpm_back.services.exam.test_attempts import (
    delete_attempt_by_id,
    get_attempt_admin_detail,
    list_attempts_by_test,
)

__all__ = [
    "list_test_sessions_admin",
    "get_test_session_admin_detail",
    "delete_test_session_admin",
    "list_test_attempts_admin",
    "get_test_attempt_admin_detail",
    "delete_test_attempt_admin",
]


def list_test_sessions_admin(test_id):
    sessions = get_test_sessions_by_test(test_id)
    names = get_student_names_by_ids([s.get("studentId") for s in sessions])
    items = []
    for s in sessions:
        sid = s.get("studentId")
        answers_total = s.get("answersCount")
        items.append({
            "sessionId": s.get("id"),
            "studentId": sid,
            "studentFullName": resolve_student_name(sid, names) or f"Студент #{sid}",
            "testTitle": s.get("testTitle"),
            "score": s.get("score"),
            "completedAt": s.get("completedAt"),
            "timeSpentMinutes": s.get("timeSpentMinutes"),
            "answersCount": answers_total,
        })
    return {
        "testId": str(test_id),
        "sessions": items,
        "total": len(items),
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


def list_test_attempts_admin(test_id, status_filter=None):
    test = get_test_by_id(test_id)
    if not test:
        return {"success": False, "error": "test_not_found"}

    attempts = list_attempts_by_test(test_id, status_filter=status_filter)
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
        "total": len(items),
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
