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
    pass_homework_bulk,
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
from cpm_back.config import config
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from cpm_back.services.homework_files import HomeworkWorkflow, HomeworkWorkflowError

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
    data = request.get_json() or {}
    proctor_id = current_user.get('id') if current_user.get('role') == 'proctor' else data.get('proctorId')
    if not proctor_id:
        return jsonify({'status': False, 'error': 'proctorId_required'}), 400
    answer = get_proctor_homework_sessions(proctor_id, data.get('homeworkId'))
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
    manual_result = data.get('result')
    denied = _validate_legacy_grade_access(current_user, session_id, student_id, homework_id)
    if denied:
        return denied
    answer = pass_homework(session_id, date_object, student_id, homework_id, manual_result)
    if answer.get('status'):
        _cleanup_after_legacy_grade(session_id, student_id, homework_id)
    return jsonify(answer)


@homework_bp.route('/pass_homework_bulk', methods=['POST'])
@require_role('admin', 'proctor')
def pass_hw_bulk(current_user=None):
    data = request.get_json() or {}
    date_pass = data.get('datePass')
    homework_id = data.get('homeworkId')
    proctor_id = current_user.get('id') if current_user.get('role') == 'proctor' else data.get('proctorId')
    if not date_pass or not homework_id or not proctor_id:
        return jsonify({'status': False, 'error': 'datePass, homeworkId и proctorId обязательны'}), 400
    try:
        date_object = datetime.date.fromisoformat(str(date_pass)[:10])
        homework_id = int(homework_id)
        proctor_id = int(proctor_id)
    except (ValueError, TypeError) as exc:
        return jsonify({'status': False, 'error': str(exc)}), 400
    manual_result = data.get('result')
    if manual_result is not None:
        try:
            manual_result = int(manual_result)
        except (TypeError, ValueError):
            return jsonify({'status': False, 'error': 'invalid_result'}), 400
    denied = _validate_bulk_file_workflow(current_user, proctor_id, homework_id)
    if denied:
        return denied
    answer = pass_homework_bulk(proctor_id, homework_id, date_object, manual_result)
    if answer.get('status'):
        _cleanup_after_legacy_bulk_grade(proctor_id, homework_id)
    return jsonify(answer), 200 if answer.get('status') else 400


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
    data = request.get_json() or {}
    session_id = data.get('sessionId')
    student_id = data.get('studentId')
    homework_id = data.get('homeworkId')

    if not session_id and (student_id is None or homework_id is None):
        return jsonify({'status': False, 'error': 'sessionId или studentId+homeworkId обязательны'}), 400

    try:
        if session_id is not None:
            session_id = int(session_id)
        if student_id is not None:
            student_id = int(student_id)
        if homework_id is not None:
            homework_id = int(homework_id)
    except (TypeError, ValueError):
        return jsonify({'status': False, 'error': 'invalid_ids'}), 400

    denied = _validate_legacy_grade_access(current_user, session_id, student_id, homework_id)
    if denied:
        return denied

    answer = edit_homework_session(
        session_id=session_id,
        result=data.get('result'),
        date_pass=data.get('datePass'),
        status=data.get('status'),
        student_id=student_id,
        homework_id=homework_id,
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


def _validate_legacy_grade_access(user, session_id, student_id, homework_id):
    """Protect old grading routes from cross-group access and file-workflow bypass."""
    connection = get_db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        if session_id:
            cursor.execute('SELECT student_id,homework_id FROM homework_sessions WHERE id=%s', (session_id,))
            session = cursor.fetchone()
            if not session:
                return jsonify({'status': False, 'error': 'session_not_found'}), 404
            student_id, homework_id = session['student_id'], session['homework_id']
        if student_id is None or homework_id is None:
            return jsonify({'status': False, 'error': 'target_required'}), 400
        try:
            HomeworkWorkflow(config)._assert_actor(cursor, user, int(student_id))
        except HomeworkWorkflowError as exc:
            return jsonify({'status': False, 'error': exc.code}), exc.status
        cursor.execute('SELECT state FROM homework_submissions WHERE homework_id=%s AND student_id=%s',
                       (homework_id, student_id))
        submission = cursor.fetchone()
        if submission and submission['state'] not in ('none','uploading','processing','draft'):
            return jsonify({'status': False, 'error': 'use_file_workflow'}), 409
        return None
    finally:
        close_db_connection(connection)


def _cleanup_after_legacy_grade(session_id, student_id, homework_id):
    connection=get_db_connection()
    try:
        cursor=connection.cursor(dictionary=True)
        if session_id:
            cursor.execute('SELECT student_id,homework_id FROM homework_sessions WHERE id=%s',(session_id,));row=cursor.fetchone()
            if not row:return
            student_id,homework_id=row['student_id'],row['homework_id']
        from cpm_back.services.homework_files.cascade import queue_and_delete_submission_data
        queue_and_delete_submission_data(cursor,homework_id=homework_id,student_id=student_id)
        connection.commit()
    except Exception:
        connection.rollback()
    finally:close_db_connection(connection)


def _validate_bulk_file_workflow(user, proctor_id, homework_id):
    connection=get_db_connection()
    try:
        cursor=connection.cursor(dictionary=True)
        cursor.execute('SELECT group_id FROM proctors WHERE id=%s',(proctor_id,));proctor=cursor.fetchone()
        if not proctor:return jsonify({'status':False,'error':'proctor_not_found'}),404
        if user.get('role')=='proctor' and int(user['id'])!=int(proctor_id):return jsonify({'status':False,'error':'forbidden'}),403
        cursor.execute("SELECT COUNT(*) count FROM homework_submissions sub JOIN students s ON s.id=sub.student_id WHERE sub.homework_id=%s AND s.group_id=%s AND sub.state IN ('submitted','in_review','revision_requested','graded')",(homework_id,proctor['group_id']))
        if cursor.fetchone()['count']:return jsonify({'status':False,'error':'use_file_workflow'}),409
        return None
    finally:close_db_connection(connection)


def _cleanup_after_legacy_bulk_grade(proctor_id,homework_id):
    connection=get_db_connection()
    try:
        cursor=connection.cursor(dictionary=True);cursor.execute('SELECT group_id FROM proctors WHERE id=%s',(proctor_id,));row=cursor.fetchone()
        if not row:return
        cursor.execute('SELECT id FROM students WHERE group_id=%s',(row['group_id'],))
        from cpm_back.services.homework_files.cascade import queue_and_delete_submission_data
        for student in cursor.fetchall():queue_and_delete_submission_data(cursor,homework_id=homework_id,student_id=student['id'])
        connection.commit()
    except Exception:connection.rollback()
    finally:close_db_connection(connection)
