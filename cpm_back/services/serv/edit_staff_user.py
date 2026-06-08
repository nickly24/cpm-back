from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

from .staff_credentials import STAFF_ROLES, ROLE_TABLES

_UNSET = object()


def edit_staff_user(role, user_id, full_name=None, group_id=_UNSET, login=None):
    if role not in STAFF_ROLES:
        return {"status": False, "error": "Недопустимая роль"}

    if full_name is None and group_id is _UNSET and login is None:
        return {
            "status": False,
            "error": "Необходимо указать хотя бы одно поле для обновления",
        }

    connection = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        table_name = ROLE_TABLES[role]

        cursor.execute(f"SELECT * FROM {table_name} WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            return {"status": False, "error": f"Пользователь с ID {user_id} не найден"}

        update_fields = []
        update_values = []

        if full_name is not None:
            full_name = full_name.strip()
            if not full_name:
                return {"status": False, "error": "ФИО не может быть пустым"}
            update_fields.append("full_name = %s")
            update_values.append(full_name)

        if role == "proctor" and group_id is not _UNSET:
            if group_id:
                cursor.execute("SELECT id FROM `groups` WHERE id = %s", (group_id,))
                if not cursor.fetchone():
                    return {"status": False, "error": f"Группа с ID {group_id} не найдена"}
            update_fields.append("group_id = %s")
            update_values.append(group_id)

        if update_fields:
            update_values.append(user_id)
            cursor.execute(
                f"UPDATE {table_name} SET {', '.join(update_fields)} WHERE id = %s",
                tuple(update_values),
            )

        if login is not None:
            login = login.strip()
            if not login:
                return {"status": False, "error": "Логин не может быть пустым"}

            cursor.execute(
                "SELECT ref_id FROM auth_users WHERE username = %s AND NOT (role = %s AND ref_id = %s)",
                (login, role, user_id),
            )
            if cursor.fetchone():
                return {"status": False, "error": "Логин уже занят"}

            cursor.execute(
                "SELECT username FROM auth_users WHERE role = %s AND ref_id = %s",
                (role, user_id),
            )
            auth_row = cursor.fetchone()
            if auth_row:
                cursor.execute(
                    "UPDATE auth_users SET username = %s WHERE role = %s AND ref_id = %s",
                    (login, role, user_id),
                )
            else:
                return {
                    "status": False,
                    "error": "Учётная запись не найдена. Сбросьте пароль, чтобы создать логин.",
                }

        connection.commit()

        cursor.execute(f"SELECT * FROM {table_name} WHERE id = %s", (user_id,))
        updated = cursor.fetchone()

        cursor.execute(
            "SELECT username FROM auth_users WHERE role = %s AND ref_id = %s",
            (role, user_id),
        )
        auth_row = cursor.fetchone()

        return {
            "status": True,
            "message": "Данные пользователя обновлены",
            "user_data": {
                "id": updated["id"],
                "full_name": updated["full_name"],
                "group_id": updated.get("group_id") if role == "proctor" else None,
                "login": auth_row["username"] if auth_row else None,
            },
        }
    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": f"Ошибка базы данных: {err}"}
    finally:
        if connection:
            close_db_connection(connection)
