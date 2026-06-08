from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

from .staff_credentials import (
    STAFF_ROLES,
    ROLE_TABLES,
    generate_password,
    generate_staff_login,
    hash_password,
    parse_person_name,
)


def add_staff_user(role, full_name, group_id=None):
    if role not in STAFF_ROLES:
        return {"status": False, "error": "Недопустимая роль"}

    full_name = (full_name or "").strip()
    if not full_name:
        return {"status": False, "error": "Поле full_name обязательно"}

    person = parse_person_name(full_name)
    if not person:
        return {"status": False, "error": "Необходимо указать имя и фамилию"}

    first_name, last_name = person
    connection = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if role == "proctor" and group_id is not None:
            cursor.execute("SELECT id FROM `groups` WHERE id = %s", (group_id,))
            if not cursor.fetchone():
                return {"status": False, "error": f"Группа с ID {group_id} не найдена"}

        table_name = ROLE_TABLES[role]
        if role == "proctor":
            cursor.execute(
                f"INSERT INTO {table_name} (full_name, group_id) VALUES (%s, %s)",
                (full_name, group_id),
            )
        else:
            cursor.execute(
                f"INSERT INTO {table_name} (full_name) VALUES (%s)",
                (full_name,),
            )

        user_id = cursor.lastrowid
        login = generate_staff_login(cursor, role, first_name, last_name)
        password = generate_password()

        cursor.execute(
            """
            INSERT INTO auth_users (username, password, ref_id, role)
            VALUES (%s, %s, %s, %s)
            """,
            (login, hash_password(password), user_id, role),
        )

        connection.commit()

        user_data = {
            "id": user_id,
            "full_name": full_name,
            "login": login,
            "password": password,
            "group_id": group_id if role == "proctor" else None,
        }

        return {
            "status": True,
            "message": "Пользователь успешно создан",
            "user_data": user_data,
        }
    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": f"Ошибка базы данных: {err}"}
    finally:
        if connection:
            close_db_connection(connection)
