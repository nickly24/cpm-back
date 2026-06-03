from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

from .school_schema import require_schools_schema
from .school_utils import fetch_school_row, serialize_school


def add_school(name, short_name=None, notes=None):
    connection = None
    try:
        name = (name or "").strip()
        if not name:
            return {"status": False, "error": "Поле 'name' обязательно"}

        short_name = short_name.strip() if short_name else None
        notes = notes.strip() if notes else None

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        schema_err = require_schools_schema(cursor)
        if schema_err:
            return schema_err

        cursor.execute("SELECT id FROM schools WHERE name = %s", (name,))
        if cursor.fetchone():
            return {"status": False, "error": "Школа с таким названием уже существует"}

        cursor.execute(
            """
            INSERT INTO schools (name, short_name, notes)
            VALUES (%s, %s, %s)
            """,
            (name, short_name, notes),
        )
        school_id = cursor.lastrowid
        connection.commit()

        row = fetch_school_row(cursor, school_id)
        return {
            "status": True,
            "message": "Школа успешно создана",
            "school": serialize_school(row, student_count=0),
        }

    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": f"Ошибка базы данных: {err}"}

    finally:
        if connection:
            close_db_connection(connection)
