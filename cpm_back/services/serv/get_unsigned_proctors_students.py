from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_unassigned_students_and_proctors():
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        # Студенты без группы
        cursor.execute("SELECT id, full_name FROM students WHERE group_id IS NULL")
        students = cursor.fetchall()
        unassigned_students = [{"student_id": s["id"], "full_name": s["full_name"]} for s in students]

        # Прокторы без группы
        cursor.execute("SELECT id, full_name FROM proctors WHERE group_id IS NULL")
        proctors = cursor.fetchall()
        unassigned_proctors = [{"proctor_id": p["id"], "full_name": p["full_name"]} for p in proctors]

        return {
            "status": True,
            "unassigned_students": unassigned_students,
            "unassigned_proctors": unassigned_proctors
        }

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "unassigned_students": [], "unassigned_proctors": []}

    finally:
        if connection:
            close_db_connection(connection)