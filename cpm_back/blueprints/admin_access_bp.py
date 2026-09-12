from flask import Blueprint, jsonify, request
from mysql.connector import IntegrityError
from cpm_back.auth import require_role
from cpm_back.services import admin_access

admin_access_bp = Blueprint('admin_access', __name__, url_prefix='/api/admin-access')


def body():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError('Передайте объект с данными')
    return payload


@admin_access_bp.errorhandler(ValueError)
def invalid(exc):
    return jsonify({'status': False, 'error': str(exc)}), 400


@admin_access_bp.errorhandler(LookupError)
def missing(exc):
    return jsonify({'status': False, 'error': str(exc)}), 404


@admin_access_bp.errorhandler(IntegrityError)
def conflict(exc):
    message = 'Название роли или логин уже используются' if exc.errno == 1062 else 'Запись связана с другими данными'
    return jsonify({'status': False, 'error': message}), 409


@admin_access_bp.get('')
@require_role('admin')
def index(current_user=None):
    return jsonify(admin_access.list_access())


@admin_access_bp.post('/roles')
@require_role('admin')
def create_role(current_user=None):
    return jsonify(admin_access.save_role(body())), 201


@admin_access_bp.put('/roles/<int:role_id>')
@require_role('admin')
def update_role(role_id, current_user=None):
    return jsonify(admin_access.save_role(body(), role_id))


@admin_access_bp.delete('/roles/<int:role_id>')
@require_role('admin')
def remove_role(role_id, current_user=None):
    return jsonify(admin_access.delete_role(role_id))


@admin_access_bp.post('/users')
@require_role('admin')
def create_user(current_user=None):
    return jsonify(admin_access.save_user(body())), 201


@admin_access_bp.put('/users/<int:user_id>')
@require_role('admin')
def update_user(user_id, current_user=None):
    return jsonify(admin_access.save_user(body(), user_id))


@admin_access_bp.delete('/users/<int:user_id>')
@require_role('admin')
def remove_user(user_id, current_user=None):
    return jsonify(admin_access.delete_user(user_id))
