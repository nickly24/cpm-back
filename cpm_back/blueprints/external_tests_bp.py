"""
Внешние тесты (tests_out): по направлению, по студенту и направлению.
"""
from flask import Blueprint, jsonify
from cpm_back.auth import require_auth, require_self_or_role
from cpm_back.services.exam.get_external_tests import (
    get_external_tests_with_results_by_student,
    get_all_external_tests_by_direction_for_admin,
)

external_tests_bp = Blueprint('external_tests', __name__, url_prefix='')


@external_tests_bp.route('/external-tests/direction/<direction_id>', methods=['GET'])
@require_auth
def by_direction(direction_id, current_user=None):
    student_id = current_user.get('id') if current_user else None
    if student_id and current_user.get('role') == 'student':
        external_tests = get_external_tests_with_results_by_student(direction_id, student_id)
    else:
        external_tests = get_all_external_tests_by_direction_for_admin(direction_id)
    return jsonify(external_tests)


@external_tests_bp.route('/external-tests/student/<student_id>/direction/<direction_id>', methods=['GET'])
@require_self_or_role('student_id', 'admin')
def for_student(student_id, direction_id, current_user=None):
    return jsonify(get_external_tests_with_results_by_student(direction_id, student_id))
