from cpm_back.db.mysql_pool import close_db_connection, get_db_connection


def _fetch_group(cursor, group_id):
    cursor.execute("SELECT id, name FROM `groups` WHERE id = %s", (group_id,))
    return cursor.fetchone()


def add_group(name):
    connection = None
    try:
        name = (name or "").strip()
        if not name:
            return {"status": False, "error": "Поле 'name' обязательно"}

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("INSERT INTO `groups` (name) VALUES (%s)", (name,))
        group_id = cursor.lastrowid
        connection.commit()

        return {
            "status": True,
            "message": "Группа создана",
            "group": {
                "group_id": group_id,
                "group_name": name,
            },
        }

    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": f"Ошибка базы данных: {err}"}

    finally:
        if connection:
            close_db_connection(connection)


def edit_group(group_id, name):
    connection = None
    try:
        name = (name or "").strip()
        if not name:
            return {"status": False, "error": "Название группы не может быть пустым"}

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        group = _fetch_group(cursor, group_id)
        if not group:
            return {"status": False, "error": f"Группа с ID {group_id} не найдена"}

        cursor.execute(
            "UPDATE `groups` SET name = %s WHERE id = %s",
            (name, group_id),
        )
        connection.commit()

        return {
            "status": True,
            "message": "Название группы обновлено",
            "group": {
                "group_id": group_id,
                "group_name": name,
            },
        }

    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": f"Ошибка базы данных: {err}"}

    finally:
        if connection:
            close_db_connection(connection)


def delete_group(group_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        group = _fetch_group(cursor, group_id)
        if not group:
            return {"status": False, "error": f"Группа с ID {group_id} не найдена"}

        cursor.execute(
            "UPDATE homework_submissions sub JOIN students s ON s.id=sub.student_id SET sub.state='submitted',sub.reviewer_role=NULL,sub.reviewer_id=NULL WHERE s.group_id=%s AND sub.state='in_review' AND sub.reviewer_role='proctor'",
            (group_id,),
        )
        cursor.execute(
            "UPDATE students SET group_id = NULL WHERE group_id = %s",
            (group_id,),
        )
        students_unlinked = cursor.rowcount

        cursor.execute(
            "UPDATE proctors SET group_id = NULL WHERE group_id = %s",
            (group_id,),
        )
        proctors_unlinked = cursor.rowcount

        cursor.execute("DELETE FROM `groups` WHERE id = %s", (group_id,))
        connection.commit()

        return {
            "status": True,
            "message": "Группа удалена",
            "students_unlinked": students_unlinked,
            "proctors_unlinked": proctors_unlinked,
        }

    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": f"Ошибка базы данных: {err}"}

    finally:
        if connection:
            close_db_connection(connection)
