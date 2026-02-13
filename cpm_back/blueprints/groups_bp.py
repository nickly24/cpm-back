"""
Группы: список со студентами и прокторами, список групп, неназначенные, назначение/снятие.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role
from cpm_back.services.serv import (
    merge_groups_students_proctors,
    get_all_groups,
    get_unassigned_students_and_proctors,
    reset_group_for_user,
    assign_proctor_to_group,
    assign_student_to_group,
)

groups_bp = Blueprint('groups', __name__, url_prefix='/api')


@groups_bp.route('/get-groups-students', methods=['GET'])
@require_role('admin')
def groups_students(current_user=None):
    return jsonify(merge_groups_students_proctors())


@groups_bp.route('/get-groups', methods=['GET'])
@require_role('admin')
def list_groups(current_user=None):
    return jsonify(get_all_groups())


@groups_bp.route('/get-unsigned-proctors-students', methods=['GET'])
@require_role('admin')
def unsigned(current_user=None):
    return jsonify(get_unassigned_students_and_proctors())


@groups_bp.route('/remove-groupd-id-student', methods=['POST'])
@require_role('admin')
def remove_student(current_user=None):
    data = request.get_json()
    return jsonify(reset_group_for_user('student', data.get('studentId')))


@groups_bp.route('/remove-groupd-id-proctor', methods=['POST'])
@require_role('admin')
def remove_proctor(current_user=None):
    data = request.get_json()
    return jsonify(reset_group_for_user('proctor', data.get('proctorId')))


@groups_bp.route('/change-group-proctor', methods=['POST'])
@require_role('admin')
def change_proctor(current_user=None):
    data = request.get_json()
    return jsonify(assign_proctor_to_group(data.get('proctorId'), data.get('groupId')))


@groups_bp.route('/change-group-student', methods=['POST'])
@require_role('admin')
def change_student(current_user=None):
    data = request.get_json()
    return jsonify(assign_student_to_group(data.get('studentId'), data.get('groupId')))
