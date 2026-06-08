"""
Пользователи по роли, удаление пользователя.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role
from cpm_back.services.serv import (
    add_staff_user,
    delete_user,
    edit_staff_user,
    get_users_by_role,
    reset_staff_password,
)
from cpm_back.services.serv.edit_staff_user import _UNSET as STAFF_GROUP_UNSET

users_bp = Blueprint('users', __name__, url_prefix='/api')


@users_bp.route('/get-users-by-role', methods=['POST'])
@require_role('admin')
def by_role(current_user=None):
    data = request.get_json()
    role = data.get('role')
    if not role:
        return jsonify({'status': False, 'error': 'Поле "role" обязательно'}), 400
    return jsonify(get_users_by_role(role))


@users_bp.route('/delete-user', methods=['POST'])
@require_role('admin')
def delete(current_user=None):
    data = request.get_json()
    role = data.get('role')
    user_id = data.get('userId')
    if not role or not user_id:
        return jsonify({'status': False, 'error': 'role и userId обязательны'}), 400
    return jsonify(delete_user(role, user_id))


@users_bp.route('/add-staff-user', methods=['POST'])
@require_role('admin')
def add_staff(current_user=None):
    data = request.get_json() or {}
    role = data.get('role')
    full_name = data.get('full_name')
    if not role or not full_name:
        return jsonify({'status': False, 'error': 'role и full_name обязательны'}), 400

    group_id = data.get('group_id')
    if group_id is not None and group_id != '':
        try:
            group_id = int(group_id)
        except (TypeError, ValueError):
            return jsonify({'status': False, 'error': 'group_id должен быть числом'}), 400
    else:
        group_id = None

    answer = add_staff_user(role, full_name, group_id=group_id)
    return jsonify(answer), 200 if answer.get('status') else 400


@users_bp.route('/edit-staff-user', methods=['PUT'])
@require_role('admin')
def edit_staff(current_user=None):
    data = request.get_json() or {}
    role = data.get('role')
    user_id = data.get('user_id')
    if not role or not user_id:
        return jsonify({'status': False, 'error': 'role и user_id обязательны'}), 400

    group_id = STAFF_GROUP_UNSET
    if 'group_id' in data:
        raw_group_id = data.get('group_id')
        if raw_group_id in (None, ''):
            group_id = None
        else:
            try:
                group_id = int(raw_group_id)
            except (TypeError, ValueError):
                return jsonify({'status': False, 'error': 'group_id должен быть числом'}), 400

    answer = edit_staff_user(
        role,
        user_id,
        full_name=data.get('full_name'),
        group_id=group_id,
        login=data.get('login'),
    )
    return jsonify(answer), 200 if answer.get('status') else 400


@users_bp.route('/reset-staff-password', methods=['POST'])
@require_role('admin')
def reset_staff(current_user=None):
    data = request.get_json() or {}
    role = data.get('role')
    user_id = data.get('user_id')
    if not role or not user_id:
        return jsonify({'status': False, 'error': 'role и user_id обязательны'}), 400

    answer = reset_staff_password(role, user_id)
    return jsonify(answer), 200 if answer.get('status') else 400
