"""
Проверка логина/пароля по MySQL (auth_users + таблицы по ролям).
"""
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection


def auth(username, password):
    cnx = None
    try:
        cnx = get_db_connection()
        cur = cnx.cursor()
        cur.execute("SELECT * FROM auth_users WHERE username = %s AND password = %s", (username, password))
        row = cur.fetchall()
        if len(row) == 0:
            return {'status': False}
        if row[0][4] == 'student':
            cur.execute("SELECT * FROM students WHERE id = %s", (row[0][2],))
            data = cur.fetchone()
            return {
                'status': True,
                'res': {'role': 'student', 'id': data[0], 'full_name': data[1], 'group_id': data[2]}
            }
        if row[0][4] == 'proctor':
            cur.execute("SELECT * FROM proctors WHERE id = %s", (row[0][2],))
            data = cur.fetchone()
            return {
                'status': True,
                'res': {'role': 'proctor', 'id': data[0], 'full_name': data[1], 'group_id': data[2]}
            }
        if row[0][4] == 'examinator':
            cur.execute("SELECT * FROM examinators WHERE id = %s", (row[0][2],))
            data = cur.fetchone()
            return {
                'status': True,
                'res': {'role': 'examinator', 'id': data[0], 'full_name': data[1]}
            }
        if row[0][4] == 'admin':
            cur.execute("SELECT * FROM admins WHERE id = %s", (row[0][2],))
            data = cur.fetchone()
            return {
                'status': True,
                'res': {'role': 'admin', 'id': data[0], 'full_name': data[1]}
            }
        if row[0][4] == 'supervisor':
            cur.execute("SELECT * FROM supervisors WHERE id = %s", (row[0][2],))
            data = cur.fetchone()
            return {
                'status': True,
                'res': {'role': 'supervisor', 'id': data[0], 'full_name': data[1]}
            }
        return {'status': False}
    except Exception as e:
        print(f"Ошибка в auth: {str(e)}")
        return {'status': False}
    finally:
        if cnx:
            close_db_connection(cnx)
