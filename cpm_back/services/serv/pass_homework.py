from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
import datetime

def pass_homework(session_id, date_pass, student_id=None, homework_id=None):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        # 1. Получаем homework_id - либо из session_id, либо напрямую
        if session_id:
            cursor.execute("SELECT homework_id, student_id FROM homework_sessions WHERE id = %s", (session_id,))
            session = cursor.fetchone()
            
            if not session:
                print("Сессия не найдена.")
                return {"status": False}

            homework_id = session["homework_id"]
            student_id = session["student_id"]
        else:
            # Если session_id не передан, используем прямые параметры
            if not homework_id or not student_id:
                print("Необходимо указать либо session_id, либо homework_id и student_id.")
                return {"status": False}

        # 2. Получаем deadline из homework
        cursor.execute("SELECT deadline FROM homework WHERE id = %s", (homework_id,))
        homework = cursor.fetchone()

        if not homework:
            print("Домашняя работа не найдена.")
            return {"status": False}

        deadline = homework["deadline"]

        # 3. Считаем просрочку
        result = 100
        if date_pass > deadline:
            delta_days = (date_pass - deadline).days
            result -= delta_days * 5
            if result < 0:
                result = 0

        # 4. Обновляем или создаем homework_session
        if session_id:
            # Если сессия существует, обновляем её
            update_query = """
                UPDATE homework_sessions 
                SET status = 1, result = %s, date_pass = %s 
                WHERE id = %s
            """
            cursor.execute(update_query, (result, date_pass, session_id))
        else:
            # Если сессии нет, создаем новую
            insert_query = """
                INSERT INTO homework_sessions (status, result, homework_id, student_id, date_pass)
                VALUES (1, %s, %s, %s, %s)
            """
            cursor.execute(insert_query, (result, homework_id, student_id, date_pass))
        
        connection.commit()

        print(f"Оценка выставлена: {result} баллов")
        return {"status": True, "result": result}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        if connection:
            connection.rollback()
        return {"status": False}

    finally:
        if connection:
            close_db_connection(connection)


