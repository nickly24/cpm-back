"""
Проверка логина/пароля по MySQL (auth_users + таблицы по ролям).
"""
from werkzeug.security import check_password_hash
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection


ROLE_TABLES = {
    'student': 'students',
    'proctor': 'proctors',
    'examinator': 'examinators',
    'admin': 'admins',
    'supervisor': 'supervisors',
}


def _password_matches(stored_password, candidate_password):
    if not stored_password:
        return False
    try:
        if check_password_hash(stored_password, candidate_password):
            return True
    except ValueError:
        pass
    # Совместимость со старыми plaintext-паролями в auth_users.
    return stored_password == candidate_password


def auth(username, password):
    cnx = None
    try:
        cnx = get_db_connection()
        cur = cnx.cursor(dictionary=True)
        cur.execute(
            "SELECT username, password, ref_id, role FROM auth_users WHERE username = %s LIMIT 1",
            (username,)
        )
        user_row = cur.fetchone()
        if not user_row or not _password_matches(user_row.get('password'), password):
            return {'status': False}

        role = user_row.get('role')
        table = ROLE_TABLES.get(role)
        if not table:
            return {'status': False}

        cur.execute(f"SELECT * FROM {table} WHERE id = %s", (user_row.get('ref_id'),))
        data = cur.fetchone()
        if not data:
            return {'status': False}

        result = {'role': role, 'id': data.get('id'), 'full_name': data.get('full_name')}
        if role in ('student', 'proctor'):
            result['group_id'] = data.get('group_id')
        return {'status': True, 'res': result}

        return {'status': False}
    except Exception as e:
        print(f"Ошибка в auth: {str(e)}")
        return {'status': False}
    finally:
        if cnx:
            close_db_connection(cnx)
