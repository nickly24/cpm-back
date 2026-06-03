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
    get_groups_overview,
    get_group_members,
    search_groups_and_members,
    add_group,
    edit_group,
    delete_group,
)

groups_bp = Blueprint('groups', __name__, url_prefix='/api')


@groups_bp.route('/get-groups-students', methods=['GET'])
@require_role('admin')
def groups_students(current_user=None):
    """Legacy: полный снимок всех групп (оптимизирован, 3 SQL вместо 2N+1)."""
    return jsonify(merge_groups_students_proctors())


@groups_bp.route('/groups/overview', methods=['GET'])
@require_role('admin')
def groups_overview(current_user=None):
    """
    Лёгкий список: группа + проктор + student_count.
    Query: search, page, limit
    """
    answer = get_groups_overview(
        search=request.args.get('search') or request.args.get('q'),
        page=request.args.get('page', 1),
        limit=request.args.get('limit', 20),
    )
    return jsonify(answer), 200 if answer.get('status') else 500


@groups_bp.route('/groups/<int:group_id>/members', methods=['GET'])
@require_role('admin')
def group_members(group_id, current_user=None):
    """Состав одной группы — подгрузка по клику."""
    answer = get_group_members(group_id)
    return jsonify(answer), 200 if answer.get('status') else 404


@groups_bp.route('/groups/search', methods=['GET'])
@require_role('admin')
def groups_search(current_user=None):
    """
    Поиск групп и учеников по имени.
    Query: q (обяз.), limit
    """
    answer = search_groups_and_members(
        query=request.args.get('q') or request.args.get('search'),
        limit=request.args.get('limit', 50),
    )
    return jsonify(answer), 200 if answer.get('status') else 400


@groups_bp.route('/add-group', methods=['POST'])
@require_role('admin')
def create_group(current_user=None):
    data = request.get_json() or {}
    answer = add_group(data.get('name'))
    return jsonify(answer), 200 if answer.get('status') else 400


@groups_bp.route('/edit-group', methods=['PUT'])
@require_role('admin')
def update_group(current_user=None):
    data = request.get_json() or {}
    group_id = data.get('groupId') or data.get('group_id')
    if not group_id:
        return jsonify({"status": False, "error": "groupId обязателен"}), 400
    answer = edit_group(group_id, data.get('name'))
    return jsonify(answer), 200 if answer.get('status') else 400


@groups_bp.route('/delete-group', methods=['POST'])
@require_role('admin')
def remove_group(current_user=None):
    data = request.get_json() or {}
    group_id = data.get('groupId') or data.get('group_id')
    if not group_id:
        return jsonify({"status": False, "error": "groupId обязателен"}), 400
    answer = delete_group(group_id)
    return jsonify(answer), 200 if answer.get('status') else 400


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
