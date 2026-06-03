from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
import datetime

def create_homework_and_sessions(
    homework_name, homework_type, deadline_str, published=True
):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        # Преобразуем строку в дату
        # Ожидаем, что строка приходит в формате 'YYYY-MM-DD' (стандартный формат для HTML input type="date")
        deadline = datetime.datetime.strptime(deadline_str, "%Y-%m-%d").date()

        # Добавляем новую домашнюю работу в таблицу homework с дедлайном
        published_val = 1 if published else 0
        insert_homework_query = (
            "INSERT INTO homework (name, type, deadline, published) VALUES (%s, %s, %s, %s)"
        )
        cursor.execute(
            insert_homework_query,
            (homework_name, homework_type, deadline, published_val),
        )
        connection.commit()

        homework_id = cursor.lastrowid
        print(f"Домашняя работа добавлена с id: {homework_id}")

        # Получаем всех студентов
        cursor.execute("SELECT id FROM students")
        students = cursor.fetchall()

        if not students:
            print("Нет студентов для создания сессий.")
            return {"status": True, "homeworkId": homework_id}

        # Создаём записи в homework_sessions для каждого студента
        insert_session_query = """
            INSERT INTO homework_sessions (status, result, homework_id, student_id)
            VALUES (%s, %s, %s, %s)
        """
        for (student_id,) in students:
            cursor.execute(insert_session_query, (0, 0, homework_id, student_id))
        connection.commit()

        print(f"Созданы сессии для всех студентов.")
        return {"status": True, "homeworkId": homework_id}

    except ValueError:
        print("Ошибка формата даты. Ожидается строка в формате YYYY-MM-DD.")
        return {'status': False, 'error': 'Неверный формат даты'}
    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        if connection:
            connection.rollback()
        return {'status': False, 'error': str(err)}

    finally:
        if connection:
            close_db_connection(connection)