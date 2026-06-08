from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

from .staff_credentials import (
    STAFF_ROLES,
    ROLE_TABLES,
    generate_password,
    generate_staff_login,
    hash_password,
    parse_person_name,
)


def reset_staff_password(role, user_id):
    if role not in STAFF_ROLES:
        return {"status": False, "error": "Недопустимая роль"}

    connection = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        table_name = ROLE_TABLES[role]

        cursor.execute(f"SELECT id, full_name FROM {table_name} WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            return {"status": False, "error": f"Пользователь с ID {user_id} не найден"}

        password = generate_password()
        password_hash = hash_password(password)

        cursor.execute(
            "SELECT username FROM auth_users WHERE role = %s AND ref_id = %s",
            (role, user_id),
        )
        auth_row = cursor.fetchone()

        if auth_row:
            login = auth_row["username"]
            cursor.execute(
                "UPDATE auth_users SET password = %s WHERE role = %s AND ref_id = %s",
                (password_hash, role, user_id),
            )
        else:
            person = parse_person_name(user["full_name"])
            if not person:
                return {
                    "status": False,
                    "error": "Некорректное ФИО — невозможно сгенерировать логин",
                }
            login = generate_staff_login(cursor, role, person[0], person[1])
            cursor.execute(
                """
                INSERT INTO auth_users (username, password, ref_id, role)
                VALUES (%s, %s, %s, %s)
                """,
                (login, password_hash, user_id, role),
            )

        connection.commit()

        return {
            "status": True,
            "message": "Пароль обновлён",
            "user_data": {
                "id": user_id,
                "login": login,
                "password": password,
            },
        }
    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": f"Ошибка базы данных: {err}"}
    finally:
        if connection:
            close_db_connection(connection)
