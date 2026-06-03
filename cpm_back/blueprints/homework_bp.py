"""
Домашние задания: список, сессии проктора, студента, CRUD, пагинация, ОВ-таблица.
Для студентов — отдельный GET-роут с пагинацией, домашки + сессии в одном ответе.
"""
import datetime
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_auth, require_role, require_self_or_role
from cpm_back.services.serv import (
    get_homeworks,
    get_homeworks_paginated,
    get_proctor_homework_sessions,
    pass_homework,
    get_student_homework_dashboard,
    create_homework_and_sessions,
    delete_homework,
    edit_homework_session,
    get_all_homework_results,
    get_homework_results_paginated,
    get_homework_students,
    get_ov_homework_table,
    get_homework_by_id,
    update_homework,
    toggle_homework_published,
    get_homework_overview,
)

homework_bp = Blueprint('homework', __name__, url_prefix='/api')


@homework_bp.route('/get-homeworks')
def list_homeworks():
    page = request.args.get('page', type=int, default=1)
    limit = request.args.get('limit', type=int, default=50)
    homework_type = request.args.get('type', default=None)
    search = request.args.get('search') or request.args.get('q')
    if page is not None or limit is not None or homework_type or search:
        limit = min(max(1, limit or 50), 100)
        page = max(1, page or 1)
        return jsonify(
            get_homeworks_paginated(
                page=page,
                limit=limit,
                homework_type=homework_type,
                search=search,
            )
        )
    return jsonify(get_homeworks())


@homework_bp.route('/get-homework-sessions', methods=['POST'])
@require_role('admin', 'proctor')
def proctor_sessions(current_user=None):
    data = request.get_json()
    answer = get_proctor_homework_sessions(data.get('proctorId'), data.get('homeworkId'))
    return jsonify(answer)


@homework_bp.route('/pass_homework', methods=['POST'])
@require_role('admin', 'proctor')
def pass_hw(current_user=None):
    data = request.get_json()
    date_pass = data.get('datePass')
    if not date_pass:
        return jsonify({'error': 'Поле "datePass" отсутствует'}), 400
    try:
        date_object = datetime.date.fromisoformat(date_pass)
    except ValueError:
        try:
            date_object = datetime.datetime.strptime(date_pass, '%Y-%m-%d').date()
        except ValueError as e:
            return jsonify({'error': f'Неверный формат даты: {str(e)}'}), 400
    session_id = data.get('sessionId')
    student_id = data.get('studentId')
    homework_id = data.get('homeworkId')
    answer = pass_homework(session_id, date_object, student_id, homework_id)
    return jsonify(answer)


@homework_bp.route('/get-homeworks-student', methods=['POST'])
@require_self_or_role('studentId', 'proctor')
def student_homeworks(current_user=None):
    data = request.get_json() or {}
    student_id = data.get('studentId')
    use_pagination = 'page' in data or 'limit' in data
    page = data.get('page', 1)
    limit = data.get('limit', 500 if not use_pagination else 20)
    homework_type = data.get('homework_type') or data.get('type')
    try:
        page = int(page) if page is not None else 1
        limit = int(limit) if limit is not None else (500 if not use_pagination else 20)
        limit = min(max(1, limit), 500)
        page = max(1, page)
    except (TypeError, ValueError):
        page, limit = 1, (500 if not use_pagination else 20)
    answer = get_student_homework_dashboard(student_id, page=page, limit=limit, homework_type=homework_type or None)
    return jsonify(answer)


@homework_bp.route('/homeworks/student-with-sessions', methods=['GET'])
@require_auth
def student_homeworks_with_sessions(current_user=None):
    """
    Для студентов: домашки + сессии в одном ответе с пагинацией (один запрос).
    Админский роут /get-homeworks не трогаем.
    """
    role = (current_user or {}).get('role')
    student_id = (current_user or {}).get('id')
    if role != 'student' or not student_id:
        return jsonify({'status': False, 'error': 'Доступно только для студента'}), 403
    page = request.args.get('page', type=int, default=1)
    limit = request.args.get('limit', type=int, default=6)
    homework_type = request.args.get('type') or request.args.get('homework_type')
    page = max(1, page)
    limit = min(max(1, limit), 100)
    answer = get_student_homework_dashboard(student_id, page=page, limit=limit, homework_type=homework_type)
    return jsonify(answer)


@homework_bp.route('/get-all-homework-results', methods=['GET'])
@require_role('admin')
def all_results(current_user=None):
    return jsonify(get_all_homework_results())


@homework_bp.route('/get-homework-results-paginated', methods=['POST'])
@require_role('admin')
def results_paginated(current_user=None):
    data = request.get_json() or {}
    page = max(1, int(data.get('page', 1)))
    limit = max(1, min(100, int(data.get('limit', 10))))
    filters = data.get('filters', {})
    return jsonify(get_homework_results_paginated(page, limit, filters))


@homework_bp.route('/get-homework-students', methods=['POST'])
@require_role('admin')
def homework_students(current_user=None):
    data = request.get_json() or {}
    homework_id = data.get('homework_id')
    if not homework_id:
        return jsonify({"status": False, "error": "homework_id обязателен"}), 400
    try:
        homework_id = int(homework_id)
        page = max(1, int(data.get('page', 1)))
        limit = max(1, min(200, int(data.get('limit', 50))))
    except (ValueError, TypeError):
        return jsonify({"status": False, "error": "Неверные параметры"}), 400
    return jsonify(get_homework_students(homework_id, page, limit, data.get('filters', {})))


@homework_bp.route('/edit-homework-session', methods=['POST'])
@require_role('admin', 'proctor')
def edit_session(current_user=None):
    data = request.get_json()
    if not data.get('sessionId'):
        return jsonify({'error': 'Поле "sessionId" обязательно'}), 400
    answer = edit_homework_session(
        session_id=data.get('sessionId'),
        result=data.get('result'),
        date_pass=data.get('datePass'),
        status=data.get('status')
    )
    return jsonify(answer), 200 if answer.get('status') else 400


@homework_bp.route('/homework/<int:homework_id>', methods=['GET'])
@require_role('admin')
def homework_detail(homework_id, current_user=None):
    result = get_homework_by_id(homework_id)
    if not result.get('status'):
        return jsonify({"error": result.get("error", "Not found")}), 404
    return jsonify(result['res'])


@homework_bp.route('/homework/<int:homework_id>', methods=['PUT'])
@require_role('admin')
def homework_update(homework_id, current_user=None):
    data = request.get_json() or {}
    payload = {}
    if 'name' in data or 'homeworkName' in data:
        payload['name'] = data.get('name') or data.get('homeworkName')
    if 'type' in data or 'homeworkType' in data:
        payload['type'] = data.get('type') or data.get('homeworkType')
    if 'deadline' in data:
        payload['deadline'] = data.get('deadline')
    if 'published' in data:
        payload['published'] = data.get('published')

    result = update_homework(homework_id, payload)
    if not result.get('status'):
        return jsonify({"error": result.get("error", "Update failed")}), 400
    return jsonify({
        "message": "Homework updated successfully",
        "homeworkId": homework_id,
    })


@homework_bp.route('/homework/<int:homework_id>/toggle-published', methods=['PUT'])
@require_role('admin')
def homework_toggle_published(homework_id, current_user=None):
    existing = get_homework_by_id(homework_id)
    if not existing.get('status'):
        return jsonify({"error": "Homework not found"}), 404
    result = toggle_homework_published(homework_id)
    if not result.get('status'):
        return jsonify({"error": result.get("error", "Toggle failed")}), 500
    return jsonify({
        "message": result.get("message"),
        "published": result.get("published"),
        "homeworkId": homework_id,
    })


@homework_bp.route('/homework/<int:homework_id>/overview', methods=['GET'])
@require_role('admin')
def homework_overview(homework_id, current_user=None):
    result = get_homework_overview(homework_id)
    if not result.get('status'):
        return jsonify({"error": result.get("error", "Not found")}), 404
    return jsonify({
        "homework": result.get("homework"),
        "analytics": result.get("analytics"),
    })


@homework_bp.route('/homework/<int:homework_id>/students', methods=['GET'])
@require_role('admin')
def homework_students_get(homework_id, current_user=None):
    page = max(1, request.args.get('page', type=int, default=1))
    limit = max(1, min(100, request.args.get('limit', type=int, default=10)))
    filters = {}
    search = request.args.get('search') or request.args.get('q')
    if search:
        filters['search'] = search.strip()
    status = request.args.get('status')
    if status:
        filters['status'] = status
    group = request.args.get('group')
    if group:
        filters['group'] = group
    return jsonify(get_homework_students(homework_id, page, limit, filters))


@homework_bp.route('/homework/<int:homework_id>', methods=['DELETE'])
@require_role('admin')
def homework_delete_rest(homework_id, current_user=None):
    existing = get_homework_by_id(homework_id)
    if not existing.get('status'):
        return jsonify({"error": "Homework not found"}), 404
    answer = delete_homework(homework_id)
    if not answer.get('status'):
        return jsonify({"error": "Delete failed"}), 500
    return jsonify({
        "message": "Homework deleted successfully",
        "homeworkId": homework_id,
    })


@homework_bp.route('/create-homework', methods=['POST'])
@require_role('admin')
def create_hw(current_user=None):
    data = request.get_json() or {}
    published = data.get('published', True)
    if isinstance(published, str):
        published = published.lower() in ('1', 'true', 'yes')
    answer = create_homework_and_sessions(
        data.get('homeworkName'),
        data.get('homeworkType'),
        data.get('deadline'),
        published=bool(published),
    )
    if answer.get('status') and answer.get('homeworkId'):
        return jsonify({
            **answer,
            "id": answer['homeworkId'],
        })
    return jsonify(answer)


@homework_bp.route('/delete-homework', methods=['POST'])
@require_role('admin')
def delete_hw(current_user=None):
    answer = delete_homework(request.get_json().get('homeworkId'))
    return jsonify(answer)


@homework_bp.route('/get-ov-homework-table', methods=['GET'])
@require_role('admin', 'supervisor', 'proctor')
def ov_table(current_user=None):
    return jsonify(get_ov_homework_table())
