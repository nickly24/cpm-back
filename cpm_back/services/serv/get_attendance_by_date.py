from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
import datetime

def get_attendance_by_date(date_str):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        # Преобразуем строку в дату (ожидаем формат YYYY-MM-DD)
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

        # Получаем посещаемость на заданную дату с именами студентов
        query = """
            SELECT a.id, a.date, s.id AS student_id, s.full_name
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            WHERE a.date = %s
            ORDER BY s.full_name
        """
        cursor.execute(query, (date_obj,))
        attendance_records = cursor.fetchall()

        result = [
            {
                "attendance_id": record["id"],
                "student_id": record["student_id"],
                "full_name": record["full_name"],
                "date": record["date"].isoformat()
            }
            for record in attendance_records
        ]

        return {"status": True, "res": result}

    except ValueError:
        print("Неверный формат даты. Ожидается YYYY-MM-DD.")
        return {"status": False, "res": [], "error": "Invalid date format"}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": [], "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
