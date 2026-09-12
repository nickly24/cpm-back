"""Roles and delegated accounts. All mutations are limited to the new tables/auth type."""
from contextlib import contextmanager
import secrets

from werkzeug.security import generate_password_hash
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from cpm_back.auth.admin_permissions import SECTIONS


@contextmanager
def database(write=False):
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        yield cursor
        if write:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        close_db_connection(connection)


def normalize_permissions(value):
    if not isinstance(value, dict) or set(value) - SECTIONS.keys():
        raise ValueError('Некорректный список разделов')
    result = {}
    for section, flags in value.items():
        if not isinstance(flags, dict) or set(flags) - {'view', 'edit'}:
            raise ValueError('Некорректные права раздела')
        if any(type(v) is not bool for v in flags.values()):
            raise ValueError('Права должны быть флажками просмотра и редактирования')
        edit = flags.get('edit', False)
        result[section] = {'view': flags.get('view', False) or edit, 'edit': edit}
    return result


def text_field(value, name, limit, required=True):
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ValueError(f'Некорректное поле «{name}» (до {limit} символов)')
    return value.strip()


def positive_id(value):
    if type(value) is not int or value <= 0:
        raise ValueError('Выберите роль')
    return value


def load_staff_admin(user_id, session_version=None):
    with database() as cursor:
        cursor.execute('SELECT u.id,u.full_name,u.role_id,u.is_active,u.session_version,r.name AS role_name '
                       'FROM admin_role_users u JOIN admin_roles r ON r.id=u.role_id '
                       "JOIN auth_users a ON a.ref_id=u.id AND a.role='staff_admin' WHERE u.id=%s", (user_id,))
        user = cursor.fetchone()
        if not user or not user['is_active']:
            return None
        if session_version is not None and user['session_version'] != session_version:
            return None
        cursor.execute('SELECT section,can_view,can_edit FROM admin_role_permissions WHERE role_id=%s', (user['role_id'],))
        user['permissions'] = {r['section']: {'view': bool(r['can_view']), 'edit': bool(r['can_edit'])} for r in cursor.fetchall() if r['section'] in SECTIONS}
        user['role'] = 'staff_admin'
        user['is_active'] = bool(user['is_active'])
        return user


def list_access():
    with database() as cursor:
        cursor.execute('SELECT r.*,COUNT(u.id) AS users_count FROM admin_roles r LEFT JOIN admin_role_users u ON u.role_id=r.id GROUP BY r.id ORDER BY r.name')
        roles = cursor.fetchall()
        cursor.execute('SELECT role_id,section,can_view,can_edit FROM admin_role_permissions')
        permissions = cursor.fetchall()
        for role in roles:
            role['permissions'] = {p['section']: {'view': bool(p['can_view']), 'edit': bool(p['can_edit'])} for p in permissions if p['role_id'] == role['id']}
        cursor.execute("SELECT u.id,u.full_name,u.role_id,u.is_active,r.name AS role_name,a.username AS login "
                       "FROM admin_role_users u JOIN admin_roles r ON r.id=u.role_id LEFT JOIN auth_users a ON a.ref_id=u.id AND a.role='staff_admin' ORDER BY u.full_name")
        users = cursor.fetchall()
        for user in users:
            user['is_active'] = bool(user['is_active'])
        return {'status': True, 'sections': [{'id': k, 'label': v} for k,v in SECTIONS.items()], 'roles': roles, 'users': users}


def save_role(payload, role_id=None):
    name = text_field(payload.get('name'), 'Название роли', 100)
    description = text_field(payload.get('description', ''), 'Описание', 500, False)
    permissions = normalize_permissions(payload.get('permissions', {}))
    with database(write=True) as cursor:
        if role_id is None:
            cursor.execute('INSERT INTO admin_roles (name,description) VALUES (%s,%s)', (name,description))
            role_id = cursor.lastrowid
        else:
            cursor.execute('SELECT id FROM admin_roles WHERE id=%s FOR UPDATE', (role_id,))
            if not cursor.fetchone():
                raise LookupError('Роль не найдена')
            cursor.execute('UPDATE admin_roles SET name=%s,description=%s WHERE id=%s', (name,description,role_id))
        cursor.execute('DELETE FROM admin_role_permissions WHERE role_id=%s', (role_id,))
        for section, flags in permissions.items():
            cursor.execute('INSERT INTO admin_role_permissions (role_id,section,can_view,can_edit) VALUES (%s,%s,%s,%s)',
                           (role_id,section,flags['view'],flags['edit']))
    return {'status': True, 'id': role_id}


def delete_role(role_id):
    with database(write=True) as cursor:
        cursor.execute('SELECT id FROM admin_roles WHERE id=%s FOR UPDATE', (role_id,))
        if not cursor.fetchone():
            raise LookupError('Роль не найдена')
        cursor.execute('SELECT id FROM admin_role_users WHERE role_id=%s LIMIT 1', (role_id,))
        if cursor.fetchone():
            raise ValueError('Роль назначена пользователям. Сначала назначьте им другую роль')
        cursor.execute('DELETE FROM admin_roles WHERE id=%s', (role_id,))
    return {'status': True}


def save_user(payload, user_id=None):
    name = text_field(payload.get('full_name'), 'ФИО', 100)
    login = text_field(payload.get('login'), 'Логин', 50)
    role_id = positive_id(payload.get('role_id'))
    active = payload.get('is_active', True)
    if type(active) is not bool:
        raise ValueError('Некорректное состояние аккаунта')
    password = payload.get('password')
    if password is not None and (not isinstance(password, str) or (password and not 8 <= len(password) <= 128)):
        raise ValueError('Пароль должен содержать от 8 до 128 символов')
    created = user_id is None
    if created and not password:
        password = secrets.token_urlsafe(15)
    with database(write=True) as cursor:
        # Lock role first; prevents its concurrent deletion during account creation/assignment.
        cursor.execute('SELECT id FROM admin_roles WHERE id=%s FOR SHARE', (role_id,))
        if not cursor.fetchone():
            raise ValueError('Роль не найдена')
        if created:
            cursor.execute('INSERT INTO admin_role_users (full_name,role_id,is_active) VALUES (%s,%s,%s)', (name,role_id,active))
            user_id = cursor.lastrowid
            cursor.execute("INSERT INTO auth_users (username,password,ref_id,role) VALUES (%s,%s,%s,'staff_admin')", (login,generate_password_hash(password),user_id))
        else:
            cursor.execute('SELECT id FROM admin_role_users WHERE id=%s FOR UPDATE', (user_id,))
            if not cursor.fetchone():
                raise LookupError('Пользователь не найден')
            cursor.execute('UPDATE admin_role_users SET full_name=%s,role_id=%s,is_active=%s,session_version=session_version+1 WHERE id=%s', (name,role_id,active,user_id))
            cursor.execute("SELECT id FROM auth_users WHERE role='staff_admin' AND ref_id=%s FOR UPDATE", (user_id,))
            if not cursor.fetchone():
                raise LookupError('Учётная запись не найдена')
            cursor.execute("UPDATE auth_users SET username=%s WHERE role='staff_admin' AND ref_id=%s", (login,user_id))
            if password:
                cursor.execute("UPDATE auth_users SET password=%s WHERE role='staff_admin' AND ref_id=%s", (generate_password_hash(password),user_id))
    result = {'status': True, 'id': user_id}
    if password:
        result['credentials'] = {'login': login, 'password': password}
    return result


def delete_user(user_id):
    with database(write=True) as cursor:
        cursor.execute('SELECT id FROM admin_role_users WHERE id=%s FOR UPDATE', (user_id,))
        if not cursor.fetchone():
            raise LookupError('Пользователь не найден')
        cursor.execute("DELETE FROM auth_users WHERE role='staff_admin' AND ref_id=%s", (user_id,))
        cursor.execute('DELETE FROM admin_role_users WHERE id=%s', (user_id,))
    return {'status': True}
