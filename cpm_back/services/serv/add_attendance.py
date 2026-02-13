from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
import datetime

def add_attendance(student_id, date_str, attendance_rate=1, zap_id=None):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        # Проверим существует ли студент
        cursor.execute("SELECT id, full_name FROM students WHERE id = %s", (student_id,))
        student = cursor.fetchone()
        if not student:
            return {"status": False, "error": "Студент не найден"}

        # Преобразуем строку в дату
        try:
            date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"status": False, "error": "Неверный формат даты (ожидается YYYY-MM-DD)"}

        # Проверим существует ли уже запись на эту дату для этого студента
        cursor.execute("SELECT id, attendance_rate FROM attendance WHERE student_id = %s AND date = %s", (student_id, date_obj))
        existing = cursor.fetchone()
        if existing:
            # Если есть уважительная причина (rate=2), не перезаписываем
            if existing[1] == 2:
                return {"status": False, "error": f"Студент {student[1]} уже отмечен с уважительной причиной на {date_obj.strftime('%d.%m.%Y')}"}
            else:
                return {"status": False, "error": f"Студент {student[1]} уже отмечен на {date_obj.strftime('%d.%m.%Y')}"}

        # Добавляем запись в attendance
        if zap_id:
            insert_query = "INSERT INTO attendance (date, student_id, attendance_rate, zap_id) VALUES (%s, %s, %s, %s)"
            cursor.execute(insert_query, (date_obj, student_id, attendance_rate, zap_id))
        else:
            insert_query = "INSERT INTO attendance (date, student_id, attendance_rate) VALUES (%s, %s, %s)"
            cursor.execute(insert_query, (date_obj, student_id, attendance_rate))
        
        connection.commit()

        return {"status": True}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
