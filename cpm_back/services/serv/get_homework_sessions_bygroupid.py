from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_proctor_homework_sessions(proctor_id, homework_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        # 1. Получаем group_id проектора
        cursor.execute("SELECT group_id FROM proctors WHERE id = %s", (proctor_id,))
        proctor = cursor.fetchone()

        if not proctor or proctor["group_id"] is None:
            return {"status": False, "res": []}

        group_id = proctor["group_id"]

        # 2. Получаем студентов в этой группе
        cursor.execute("SELECT id, full_name FROM students WHERE group_id = %s", (group_id,))
        students = cursor.fetchall()

        if not students:
            return {"status": False, "res": []}

        student_dict = {student["id"]: student["full_name"] for student in students}
        student_ids = list(student_dict.keys())

        # 3. Получаем homework_sessions по этим студентам и конкретной домашке
        format_strings = ','.join(['%s'] * len(student_ids))
        query = f"""
            SELECT * FROM homework_sessions 
            WHERE student_id IN ({format_strings}) AND homework_id = %s
        """
        cursor.execute(query, tuple(student_ids) + (homework_id,))
        sessions = cursor.fetchall()

        # Создаем словарь существующих сессий для быстрого поиска
        existing_sessions = {session["student_id"]: session for session in sessions}

        # Создаем результат для всех студентов группы
        result_with_names = []
        for student_id in student_ids:
            if student_id in existing_sessions:
                # Если сессия существует, используем её данные
                session_data = existing_sessions[student_id].copy()
                session_data["student_full_name"] = student_dict.get(student_id, "")
            else:
                # Если сессии нет, создаем запись с дефолтными значениями
                session_data = {
                    "id": None,
                    "status": 0,
                    "result": 0,
                    "homework_id": homework_id,
                    "student_id": student_id,
                    "date_pass": None,
                    "student_full_name": student_dict.get(student_id, "")
                }
            result_with_names.append(session_data)

        return {"status": True, "res": result_with_names}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": []}

    finally:
        if connection:
            close_db_connection(connection)

# === ПРИМЕР ===
if __name__ == "__main__":
    response = get_proctor_homework_sessions(1, 2)
    print(response)
