"""
Авторизация: login, logout, текущий пользователь (aun).
"""
from flask import Blueprint, request, jsonify, make_response
from cpm_back.auth import auth, generate_token, set_auth_cookie, clear_auth_cookie, require_auth

auth_bp = Blueprint('auth', __name__, url_prefix='/api')


@auth_bp.route('/auth', methods=['POST'])
def login():
    data = request.get_json()
    body_login = data.get('login')
    body_password = data.get('password')
    if not body_login or not body_password:
        return jsonify({'status': False, 'error': 'Логин и пароль обязательны'}), 400
    answer = auth(body_login, body_password)
    if not answer.get('status') and not answer.get('sratus'):
        return jsonify({'status': False, 'error': 'Неверный логин или пароль'}), 401
    user_data = answer.get('res', {})
    if not user_data:
        return jsonify({'status': False, 'error': 'Ошибка получения данных пользователя'}), 500
    token = generate_token(user_data)
    # Возвращаем token в body для фронта (Bearer), помимо cookie.
    response = make_response(jsonify({
        'status': True,
        'message': 'Авторизация успешна',
        'user': user_data,
        'token': token
    }))
    response = set_auth_cookie(response, token)
    return response


@auth_bp.route('/logout', methods=['POST'])
def logout():
    response = make_response(jsonify({'status': True, 'message': 'Выход выполнен успешно'}))
    response = clear_auth_cookie(response)
    return response


@auth_bp.route('/aun', methods=['POST'])
@require_auth
def aun(current_user=None):
    return jsonify({
        'status': True,
        'role': current_user.get('role'),
        'entity_id': current_user.get('id'),
        'full_name': current_user.get('full_name'),
        'group_id': current_user.get('group_id')
    })
