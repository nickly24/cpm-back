"""
Тесты (MongoDB): по направлению, по ID, CRUD, видимость, сессии.
Доступность тестов по времени считается строго по Москве.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_auth, require_role, require_self_or_role
from cpm_back.services.exam.get_directions import get_directions
from cpm_back.services.exam.get_tests_by_direction import (
    get_tests_by_direction,
)
from cpm_back.services.exam.create_test import (
    create_test,
    update_test,
    delete_test,
    get_test_by_id,
    toggle_test_visibility,
)
from cpm_back.services.exam.create_test_session import (
    create_test_session,
    get_test_session_by_id,
    get_test_sessions_by_student,
    get_test_sessions_by_test,
    get_test_session_stats,
    get_test_session_by_student_and_test,
    recalc_test_sessions,
)
from cpm_back.services.exam.get_external_tests import (
    get_external_tests_with_results_by_student,
    get_all_external_tests_by_direction_for_admin,
)

tests_bp = Blueprint('tests', __name__, url_prefix='')

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def _now_moscow():
    """Текущее время по Москве. Используется для проверки доступности тестов (даты start/end в БД — по Москве)."""
    return datetime.now(MOSCOW_TZ).replace(tzinfo=None)


@tests_bp.route('/tests/<direction>', methods=['GET'])
@require_auth
def tests_by_direction(direction, current_user=None):
    page = request.args.get('page', type=int, default=1)
    limit = request.args.get('limit', type=int, default=20)
    use_pagination = request.args.get('page') is not None or request.args.get('limit') is not None

    def _to_dt(value):
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
            except Exception:
                return None
        return None

    def _sort_date(item):
        dt = _to_dt(item.get("startDate")) or _to_dt(item.get("date"))
        return dt or datetime.min

    def _with_flags(test_item, completed_ids, role):
        is_external = bool(test_item.get("isExternal") or test_item.get("externalTest"))
        now = _now_moscow()
        start_dt = _to_dt(test_item.get("startDate"))
        end_dt = _to_dt(test_item.get("endDate"))
        is_completed = str(test_item.get("id")) in completed_ids
        is_upcoming = (not is_external) and start_dt is not None and now < start_dt
        is_active = (not is_external) and start_dt is not None and end_dt is not None and (start_dt <= now <= end_dt)
        is_missed = (not is_external) and end_dt is not None and now > end_dt and not is_completed
        can_start = (not is_external) and is_active and (not is_completed)
        can_practice = (not is_external) and is_completed
        # Для студентов просмотр результатов зависит от visible; для админа/проктора/супервайзера всегда можно.
        can_view_results = is_completed and ((role != "student") or bool(test_item.get("visible")))
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
        })
        return enriched

    # Список только в "легком" формате (без вопросов/ответов) — полный тест грузим только через /test/<id>.
    internal_tests = get_tests_by_direction(direction)

    directions = get_directions()
    direction_obj = next((d for d in directions if d.get('name') == direction), None)
    external_tests = []
    completed_ids = set()
    role = (current_user or {}).get("role")
    student_id = (current_user or {}).get("id")

    if student_id and role == "student":
        try:
            sessions = get_test_sessions_by_student(student_id)
            completed_ids = {str(s.get("testId")) for s in sessions if s.get("testId")}
        except Exception:
            completed_ids = set()

    if direction_obj:
        direction_id = direction_obj.get('id')
        try:
            if student_id and role == 'student':
                student_id = int(student_id) if student_id else None
                external_tests = get_external_tests_with_results_by_student(direction_id, student_id)
                # Для внешних тестов считаем completed, если есть результат.
                for t in external_tests:
                    if t.get("hasResult") and t.get("id"):
                        completed_ids.add(str(t.get("id")))
            else:
                external_tests = get_all_external_tests_by_direction_for_admin(direction_id)
        except Exception:
            pass

    combined = [_with_flags(t, completed_ids, role) for t in (internal_tests + external_tests)]

    counts = {
        "all": len(combined),
        "available": sum(1 for t in combined if t.get("status") == "available"),
        "upcoming": sum(1 for t in combined if t.get("status") == "upcoming"),
        "completed": sum(1 for t in combined if t.get("status") == "completed"),
        "missed": sum(1 for t in combined if t.get("status") == "missed"),
        "external": sum(1 for t in combined if t.get("status") == "external"),
    }

    if use_pagination:
        limit = min(max(1, limit), 100)
        page = max(1, page)
        total = len(combined)
        total_pages = (total + limit - 1) // limit if total else 1
        start = (page - 1) * limit
        end = start + limit
        paged = combined[start:end]
        pagination = {
            "current_page": page,
            "total_pages": total_pages,
            "total_items": total,
            "items_per_page": limit,
        }
        return jsonify({
            "tests": paged,
            "external_tests": [],
            "pagination": pagination,
            "counts": counts,
        })

    return jsonify(combined)


@tests_bp.route('/tests/<direction>/with-sessions', methods=['GET'])
@require_auth
def tests_by_direction_with_sessions(direction, current_user=None):
    """Тесты по направлению + все тест-сессии текущего студента в одном ответе (только для студентов)."""
    role = (current_user or {}).get("role")
    student_id = (current_user or {}).get("id")
    if role != "student" or not student_id:
        return jsonify({"error": "Доступно только для студента"}), 403

    def _to_dt(value):
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
            except Exception:
                return None
        return None

    def _with_flags(test_item, completed_ids):
        is_external = bool(test_item.get("isExternal") or test_item.get("externalTest"))
        now = _now_moscow()
        start_dt = _to_dt(test_item.get("startDate"))
        end_dt = _to_dt(test_item.get("endDate"))
        is_completed = str(test_item.get("id")) in completed_ids
        is_upcoming = (not is_external) and start_dt is not None and now < start_dt
        is_active = (not is_external) and start_dt is not None and end_dt is not None and (start_dt <= now <= end_dt)
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
        })
        return enriched

    internal_tests = get_tests_by_direction(direction)
    directions = get_directions()
    direction_obj = next((d for d in directions if d.get('name') == direction), None)
    external_tests = []
    completed_ids = set()

    try:
        sessions = get_test_sessions_by_student(student_id)
        completed_ids = {str(s.get("testId")) for s in sessions if s.get("testId")}
        for s in sessions:
            stats = get_test_session_stats(s["id"])
            s["stats"] = stats
    except Exception:
        sessions = []

    if direction_obj:
        direction_id = direction_obj.get('id')
        try:
            student_id_int = int(student_id) if student_id else None
            external_tests = get_external_tests_with_results_by_student(direction_id, student_id_int)
            for t in external_tests:
                if t.get("hasResult") and t.get("id"):
                    completed_ids.add(str(t.get("id")))
        except Exception:
            pass

    combined = [_with_flags(t, completed_ids) for t in (internal_tests + external_tests)]
    # Время по Москве для проверки доступности на фронте (чтобы не зависеть от времени на устройстве)
    server_time_moscow = datetime.now(MOSCOW_TZ).isoformat()
    return jsonify({"tests": combined, "sessions": sessions, "serverTimeMoscow": server_time_moscow})


@tests_bp.route('/test/<test_id>', methods=['GET'])
@require_auth
def test_by_id(test_id, current_user=None):
    test = get_test_by_id(test_id)
    if test:
        return jsonify(test)
    return jsonify({"error": "Test not found"}), 404


@tests_bp.route('/create-test', methods=['POST'])
@require_role('admin')
def create(current_user=None):
    test_data = request.get_json()
    test_id = create_test(test_data)
    return jsonify({"id": test_id})


@tests_bp.route('/test/<test_id>', methods=['PUT'])
@require_role('admin')
def update(test_id, current_user=None):
    existing_test = get_test_by_id(test_id)
    if not existing_test:
        return jsonify({"error": "Test not found"}), 404
    test_data = request.get_json()
    success = update_test(test_id, test_data)
    if success:
        recalc_stats = recalc_test_sessions(test_id)
        return jsonify({
            "message": "Test updated successfully",
            "testId": test_id,
            "recalc": recalc_stats,
        })
    return jsonify({"error": "Failed to update test"}), 500


@tests_bp.route('/test/<test_id>', methods=['DELETE'])
@require_role('admin')
def delete(test_id, current_user=None):
    existing_test = get_test_by_id(test_id)
    if not existing_test:
        return jsonify({"error": "Test not found"}), 404
    result = delete_test(test_id)
    return jsonify({
        "message": "Test and related sessions deleted successfully",
        "testId": test_id,
        "deletedSessions": result["sessions_deleted"],
        "totalDeleted": result["total_deleted"],
    })


@tests_bp.route('/test/<test_id>/toggle-visibility', methods=['PUT'])
@require_role('admin')
def toggle_visibility(test_id, current_user=None):
    existing_test = get_test_by_id(test_id)
    if not existing_test:
        return jsonify({"error": "Test not found"}), 404
    result = toggle_test_visibility(test_id)
    if result["success"]:
        return jsonify({
            "message": result["message"],
            "visible": result["visible"],
            "testId": test_id,
        })
    return jsonify({"error": result["error"]}), 500


@tests_bp.route('/create-test-session', methods=['POST'])
@require_self_or_role('studentId', 'admin')
def create_session(current_user=None):
    session_data = request.get_json()
    for field in ["studentId", "testId", "testTitle", "answers"]:
        if field not in session_data:
            return jsonify({"error": f"Missing required field: {field}"}), 400
    result = create_test_session(
        student_id=session_data["studentId"],
        test_id=session_data["testId"],
        test_title=session_data["testTitle"],
        answers=session_data["answers"],
        score=session_data.get("score"),
        time_spent_minutes=session_data.get("timeSpentMinutes"),
    )
    if not result["success"]:
        return jsonify({
            "error": result["error"],
            "message": result["message"],
            "existingSessionId": result.get("existingSessionId"),
            "existingScore": result.get("existingScore"),
            "completedAt": result.get("completedAt"),
        }), 409
    return jsonify({"id": result["sessionId"]})


@tests_bp.route('/test-session/<session_id>', methods=['GET'])
@require_auth
def get_session(session_id, current_user=None):
    session = get_test_session_by_id(session_id)
    if not session:
        return jsonify({"error": "Test session not found"}), 404
    if current_user.get('role') != 'admin' and str(session.get('studentId')) != str(current_user.get('id')):
        return jsonify({'status': False, 'error': 'Недостаточно прав доступа'}), 403
    return jsonify(session)


@tests_bp.route('/test-sessions/student/<student_id>', methods=['GET'])
@require_self_or_role('student_id', 'admin')
def sessions_by_student(student_id, current_user=None):
    return jsonify(get_test_sessions_by_student(student_id))


@tests_bp.route('/test-sessions/test/<test_id>', methods=['GET'])
@require_role('admin')
def sessions_by_test(test_id, current_user=None):
    return jsonify(get_test_sessions_by_test(test_id))


@tests_bp.route('/test-session/<session_id>/stats', methods=['GET'])
@require_auth
def session_stats(session_id, current_user=None):
    session = get_test_session_by_id(session_id)
    if not session:
        return jsonify({"error": "Test session not found"}), 404
    if current_user.get('role') != 'admin' and str(session.get('studentId')) != str(current_user.get('id')):
        return jsonify({'status': False, 'error': 'Недостаточно прав доступа'}), 403
    stats = get_test_session_stats(session_id)
    if stats:
        return jsonify(stats)
    return jsonify({"error": "Test session not found"}), 404


@tests_bp.route('/test-session/student/<student_id>/test/<test_id>', methods=['GET'])
@require_self_or_role('student_id', 'admin')
def session_by_student_and_test(student_id, test_id, current_user=None):
    session = get_test_session_by_student_and_test(student_id, test_id)
    if session:
        return jsonify(session)
    return jsonify({"error": "Test session not found"}), 404
