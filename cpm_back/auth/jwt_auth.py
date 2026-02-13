"""
JWT: генерация, проверка, декораторы авторизации и ролей.
Использует current_app.config для секрета (устанавливается в create_app).
"""
import jwt
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify, current_app


def _get_secret():
    return current_app.config.get('JWT_SECRET_KEY', 'dev-secret-key-cpm-lms-2025-change-in-production')


def _get_expiration_hours():
    return current_app.config.get('JWT_EXPIRATION_HOURS', 24)


def generate_token(user_data):
    payload = {
        'role': user_data.get('role'),
        'id': user_data.get('id'),
        'full_name': user_data.get('full_name'),
        'group_id': user_data.get('group_id'),
        'exp': datetime.utcnow() + timedelta(hours=_get_expiration_hours()),
        'iat': datetime.utcnow()
    }
    return jwt.encode(payload, _get_secret(), algorithm='HS256')


def verify_token(token):
    try:
        payload = jwt.decode(token, _get_secret(), algorithms=['HS256'])
        return {
            'role': payload.get('role'),
            'id': payload.get('id'),
            'full_name': payload.get('full_name'),
            'group_id': payload.get('group_id')
        }
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def get_token_from_request():
    auth_header = request.headers.get('Authorization', '')
    if auth_header:
        if auth_header.startswith('Bearer '):
            token = auth_header.replace('Bearer ', '', 1)
            if token:
                return token
        elif auth_header.strip():
            return auth_header.strip()
    return request.cookies.get('auth_token')


def get_current_user():
    token = get_token_from_request()
    if not token:
        return None
    return verify_token(token)


def require_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({'status': False, 'error': 'Требуется авторизация'}), 401
        kwargs['current_user'] = user
        return f(*args, **kwargs)
    return decorated_function


def require_role(*allowed_roles):
    def decorator(f):
        @wraps(f)
        @require_auth
        def decorated_function(*args, **kwargs):
            user = kwargs.get('current_user')
            if not user or user.get('role') not in allowed_roles:
                return jsonify({'status': False, 'error': 'Недостаточно прав доступа'}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_self_or_role(user_id_param='student_id', *allowed_roles):
    def decorator(f):
        @wraps(f)
        @require_auth
        def decorated_function(*args, **kwargs):
            user = kwargs.get('current_user')
            requested_id = None
            param_variants = [
                user_id_param,
                user_id_param.replace('_id', 'Id'),
                user_id_param.replace('_id', 'ID'),
            ]
            for param in param_variants:
                if param in kwargs:
                    requested_id = kwargs[param]
                    break
            if not requested_id:
                data = request.get_json() or {}
                for param in param_variants:
                    if param in data:
                        requested_id = data[param]
                        break
            if not requested_id:
                return jsonify({'status': False, 'error': f'Параметр {user_id_param} не найден'}), 400
            try:
                requested_id = int(requested_id)
                user_id = int(user.get('id'))
            except (ValueError, TypeError):
                return jsonify({'status': False, 'error': 'Неверный формат ID'}), 400
            if user_id == requested_id or user.get('role') in allowed_roles:
                return f(*args, **kwargs)
            return jsonify({'status': False, 'error': 'Недостаточно прав доступа'}), 403
        return decorated_function
    return decorator


def set_auth_cookie(response, token):
    from flask import current_app
    # Для локальной разработки по HTTP (localhost/127.0.0.1) secure-cookie не работает.
    host = (request.host or '').split(':')[0].lower()
    is_local_host = host in {'127.0.0.1', 'localhost', '0.0.0.0'}
    is_production = current_app.config.get('ENV') != 'development'
    use_secure_cookie = is_production and not is_local_host
    response.set_cookie(
        'auth_token',
        token,
        httponly=True,
        secure=use_secure_cookie,
        samesite='Lax',
        max_age=_get_expiration_hours() * 3600
    )
    return response


def clear_auth_cookie(response):
    from flask import current_app
    host = (request.host or '').split(':')[0].lower()
    is_local_host = host in {'127.0.0.1', 'localhost', '0.0.0.0'}
    is_production = current_app.config.get('ENV') != 'development'
    use_secure_cookie = is_production and not is_local_host
    response.set_cookie(
        'auth_token', '', httponly=True, secure=use_secure_cookie, samesite='Lax', max_age=0
    )
    return response
