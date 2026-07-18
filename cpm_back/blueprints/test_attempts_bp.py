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
    patch_answers_batch,
    submit_attempt,
    get_attempt_admin_detail,
    check_practice_answer,
    sync_attempt_commits,
    finalize_attempt_v2,
)
from cpm_back.services.exam.test_admin_monitoring import (
    delete_test_attempt_admin,
    force_submit_test_attempt_admin,
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
    result = start_attempt(
        current_user.get('id'), test_id, is_practice=is_practice,
        client_schema_version=data.get('clientSchemaVersion'),
    )
    if not result.get('success'):
        code = 409 if result.get('error') == 'test_already_completed' else 403
        if result.get('error') in ('test_not_found', 'invalid_student_id', 'test_has_no_questions', 'invalid_question_ids_in_test', 'duplicate_question_ids_in_test'):
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
    include_questions = request.args.get('full') == '1'
    result = patch_answer(
        attempt_id,
        current_user.get('id'),
        data,
        include_questions=include_questions,
    )
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


@test_attempts_bp.route('/test-attempt/<attempt_id>/practice-answer', methods=['POST'])
@require_role('student')
def attempt_check_practice_answer(attempt_id, current_user=None):
    result = check_practice_answer(
        attempt_id,
        current_user.get('id'),
        request.get_json() or {},
    )
    if result.get('success'):
        return jsonify(result)
    error = result.get('error')
    code = 404 if error in ('attempt_not_found', 'test_not_found') else 403
    if error in (
        'invalid_question_id', 'invalid_answer_type', 'invalid_answer_option',
        'invalid_text_answer', 'practice_attempt_required',
    ):
        code = 400
    if error in ('answer_locked', 'attempt_not_active'):
        code = 409
    return jsonify(result), code


@test_attempts_bp.route('/test-attempt/<attempt_id>/answers', methods=['POST'])
@require_role('student')
def attempt_patch_answers_batch(attempt_id, current_user=None):
    data = request.get_json() or {}
    answers = data.get('answers')
    result = patch_answers_batch(attempt_id, current_user.get('id'), answers)
    if not result.get('success') and result.get('error'):
        err = result.get('error')
        if err in ('answers_required', 'answers_batch_too_large'):
            return jsonify(result), 400
        if err in ('time_expired', 'attempt_not_active'):
            return jsonify(result), 403
        return jsonify(result), 404
    status = 200 if result.get('success') else 207
    return jsonify(result), status


@test_attempts_bp.route('/test-attempt/<attempt_id>/commits', methods=['POST'])
@require_role('student')
def attempt_sync_commits(attempt_id, current_user=None):
    data = request.get_json() or {}
    result = sync_attempt_commits(attempt_id, current_user.get('id'), data.get('commits'))
    if not result.get('success'):
        error = result.get('error')
        if error in ('commits_required', 'commits_batch_too_large'):
            return jsonify(result), 400
        if error == 'attempt_not_found':
            return jsonify(result), 404
        if error in ('time_expired', 'attempt_not_active'):
            return jsonify(result), 403
        if result.get('ackedCommitIds') or result.get('conflicts') or result.get('errors'):
            return jsonify(result)
        return jsonify(result), 400
    return jsonify(result)


@test_attempts_bp.route('/test-attempt/<attempt_id>/finalize', methods=['POST'])
@require_role('student')
def attempt_finalize_v2(attempt_id, current_user=None):
    result = finalize_attempt_v2(attempt_id, current_user.get('id'), request.get_json() or {})
    if result.get('success'):
        return jsonify(result)
    error = result.get('error')
    if error == 'attempt_not_found':
        return jsonify(result), 404
    if error in ('upload_window_closed', 'time_expired'):
        return jsonify(result), 403
    if error == 'test_already_completed':
        return jsonify(result), 409
    return jsonify(result), 400


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
        if err == 'empty_attempt_answers':
            return jsonify(result), 409
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


@test_attempts_bp.route('/test-attempt/<attempt_id>/admin/submit', methods=['POST'])
@require_role('admin')
def admin_force_submit_attempt(attempt_id, current_user=None):
    result = force_submit_test_attempt_admin(attempt_id)
    if not result.get('success'):
        err = result.get('error')
        if err == 'test_already_completed':
            return jsonify(result), 409
        code = 404 if err in ('attempt_not_found', 'test_not_found') else 400
        return jsonify(result), code
    return jsonify(result)
