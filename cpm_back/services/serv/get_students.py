from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_all_students():
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                s.id,
                s.full_name,
                s.group_id,
                s.school_id,
                s.class,
                s.tg_name,
                sch.name AS school_name,
                sch.short_name AS school_short_name
            FROM students s
            LEFT JOIN schools sch ON sch.id = s.school_id
            ORDER BY s.full_name ASC
        """)
        students = cursor.fetchall()

        result = [
            {
                "student_id": student["id"],
                "full_name": student["full_name"],
                "group_id": student["group_id"],
                "school_id": student.get("school_id"),
                "school_name": student.get("school_name"),
                "school_short_name": student.get("school_short_name"),
                "class": student["class"],
                "tg_name": student["tg_name"]
            }
            for student in students
        ]

        return {"status": True, "res": result}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": [], "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
