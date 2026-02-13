"""
Пользователи по роли, удаление пользователя.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role
from cpm_back.services.serv import get_users_by_role, delete_user

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
