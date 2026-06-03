"""
Попытки прохождения теста: start, сохранение ответов, submit.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role
from cpm_back.services.exam.test_attempts import (
    start_attempt,
    get_attempt_for_student,
    get_active_attempt,
    patch_answer,
    submit_attempt,
    get_attempt_admin_detail,
)
from cpm_back.services.exam.test_admin_monitoring import (
    delete_test_attempt_admin,
    get_test_attempt_admin_detail,
)

test_attempts_bp = Blueprint('test_attempts', __name__, url_prefix='')


@test_attempts_bp.route('/test-attempt/start', methods=['POST'])
@require_role('student')
def attempt_start(current_user=None):
    data = request.get_json() or {}
    test_id = data.get('testId')
    if not test_id:
        return jsonify({'success': False, 'error': 'testId_required'}), 400
    is_practice = bool(data.get('isPractice') or data.get('practice'))
    result = start_attempt(current_user.get('id'), test_id, is_practice=is_practice)
    if not result.get('success'):
        code = 409 if result.get('error') == 'test_already_completed' else 403
        if result.get('error') in ('test_not_found', 'invalid_student_id', 'test_has_no_questions'):
            code = 400 if result.get('error') != 'test_not_found' else 404
        if result.get('error') == 'test_not_found':
            code = 404
        if result.get('error') == 'test_not_completed':
            code = 403
        return jsonify(result), code
    return jsonify(result)


@test_attempts_bp.route('/test-attempt/active', methods=['GET'])
@require_role('student')
def attempt_active(current_user=None):
    test_id = request.args.get('testId')
    if not test_id:
        return jsonify({'success': False, 'error': 'testId_required'}), 400
    return jsonify(get_active_attempt(current_user.get('id'), test_id))


@test_attempts_bp.route('/test-attempt/<attempt_id>', methods=['GET'])
@require_role('student')
def attempt_get(attempt_id, current_user=None):
    result = get_attempt_for_student(attempt_id, current_user.get('id'))
    if not result.get('success'):
        return jsonify(result), 404
    return jsonify(result)


@test_attempts_bp.route('/test-attempt/<attempt_id>/answer', methods=['PATCH'])
@require_role('student')
def attempt_patch_answer(attempt_id, current_user=None):
    data = request.get_json() or {}
    result = patch_answer(attempt_id, current_user.get('id'), data)
    if not result.get('success'):
        err = result.get('error')
        if err == 'answer_locked':
            return jsonify(result), 403
        if err == 'time_expired':
            return jsonify(result), 403
        if err in ('invalid_question_id', 'invalid_answer_type', 'question_id_required'):
            return jsonify(result), 400
        return jsonify(result), 404
    return jsonify(result)


@test_attempts_bp.route('/test-attempt/<attempt_id>/submit', methods=['POST'])
@require_role('student')
def attempt_submit(attempt_id, current_user=None):
    result = submit_attempt(attempt_id, current_user.get('id'))
    if not result.get('success'):
        err = result.get('error')
        if err == 'test_already_completed':
            return jsonify({
                'success': False,
                'error': err,
                'existingSessionId': result.get('existingSessionId'),
                'existingScore': result.get('existingScore'),
                'completedAt': result.get('completedAt'),
            }), 409
        if err == 'time_expired':
            return jsonify(result), 403
        if err in ('test_not_started', 'test_ended'):
            return jsonify(result), 403
        return jsonify(result), 400 if err != 'attempt_not_found' else 404
    return jsonify(result)


@test_attempts_bp.route('/test-attempt/<attempt_id>/admin', methods=['GET'])
@require_role('admin')
def admin_attempt_detail(attempt_id, current_user=None):
    result = get_test_attempt_admin_detail(attempt_id)
    if not result.get('success'):
        return jsonify(result), 404
    return jsonify(result)


@test_attempts_bp.route('/test-attempt/<attempt_id>', methods=['DELETE'])
@require_role('admin')
def admin_delete_attempt(attempt_id, current_user=None):
    result = delete_test_attempt_admin(attempt_id)
    if not result.get('success'):
        code = 404 if result.get('error') in ('attempt_not_found',) else 500
        return jsonify(result), code
    return jsonify(result)
