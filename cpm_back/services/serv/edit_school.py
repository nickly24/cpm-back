from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

from .school_utils import fetch_school_row, serialize_school


def edit_school(school_id, name=None, short_name=None, notes=None, is_active=None):
    connection = None
    try:
        if all(value is None for value in (name, short_name, notes, is_active)):
            return {
                "status": False,
                "error": "Необходимо указать хотя бы одно поле для обновления",
            }

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        row = fetch_school_row(cursor, school_id)
        if not row:
            return {"status": False, "error": f"Школа с ID {school_id} не найдена"}

        update_fields = []
        update_values = []

        if name is not None:
            name = name.strip()
            if not name:
                return {"status": False, "error": "Название школы не может быть пустым"}
            cursor.execute(
                "SELECT id FROM schools WHERE name = %s AND id <> %s",
                (name, school_id),
            )
            if cursor.fetchone():
                return {"status": False, "error": "Школа с таким названием уже существует"}
            update_fields.append("name = %s")
            update_values.append(name)

        if short_name is not None:
            short_name = short_name.strip() if short_name else None
            update_fields.append("short_name = %s")
            update_values.append(short_name)

        if notes is not None:
            notes = notes.strip() if notes else None
            update_fields.append("notes = %s")
            update_values.append(notes)

        if is_active is not None:
            update_fields.append("is_active = %s")
            update_values.append(1 if is_active else 0)

        update_values.append(school_id)
        cursor.execute(
            f"UPDATE schools SET {', '.join(update_fields)} WHERE id = %s",
            tuple(update_values),
        )
        connection.commit()

        updated = fetch_school_row(cursor, school_id)
        cursor.execute("SELECT COUNT(*) AS cnt FROM students WHERE school_id = %s", (school_id,))
        student_count = cursor.fetchone()["cnt"]

        return {
            "status": True,
            "message": "Школа успешно обновлена",
            "school": serialize_school(updated, student_count=student_count),
        }

    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": f"Ошибка базы данных: {err}"}

    finally:
        if connection:
            close_db_connection(connection)
