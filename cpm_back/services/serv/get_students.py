from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_all_students():
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT id, full_name, group_id, class, tg_name FROM students ORDER BY full_name ASC")
        students = cursor.fetchall()

        result = [
            {
                "student_id": student["id"],
                "full_name": student["full_name"],
                "group_id": student["group_id"],
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
