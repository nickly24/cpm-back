from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

from .school_utils import fetch_school_row, serialize_school


def get_all_schools(active_only=False):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT
                s.id,
                s.name,
                s.short_name,
                s.notes,
                s.is_active,
                s.created_at,
                s.updated_at,
                COUNT(st.id) AS student_count
            FROM schools s
            LEFT JOIN students st ON st.school_id = s.id
        """
        params = []
        if active_only:
            query += " WHERE s.is_active = 1"
        query += """
            GROUP BY s.id, s.name, s.short_name, s.notes, s.is_active, s.created_at, s.updated_at
            ORDER BY s.name ASC
        """

        cursor.execute(query, params)
        rows = cursor.fetchall()

        return {
            "status": True,
            "res": [
                serialize_school(row, student_count=row["student_count"])
                for row in rows
            ],
        }

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": [], "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)


def get_school_by_id(school_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                s.id,
                s.name,
                s.short_name,
                s.notes,
                s.is_active,
                s.created_at,
                s.updated_at,
                COUNT(st.id) AS student_count
            FROM schools s
            LEFT JOIN students st ON st.school_id = s.id
            WHERE s.id = %s
            GROUP BY s.id, s.name, s.short_name, s.notes, s.is_active, s.created_at, s.updated_at
            """,
            (school_id,),
        )
        row = cursor.fetchone()

        if not row:
            return {"status": False, "error": f"Школа с ID {school_id} не найдена"}

        return {
            "status": True,
            "school": serialize_school(row, student_count=row["student_count"]),
        }

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
