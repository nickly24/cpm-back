from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
import datetime

def get_student_attendance(student_id, year_month):
    """
    Получает посещаемость студента за определенный месяц
    
    Args:
        student_id (str): ID студента
        year_month (str): Год и месяц в формате "YYYY-MM"
    
    Returns:
        dict: Результат с посещаемостью студента
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        # Парсим год и месяц
        try:
            year, month = year_month.split('-')
            year = int(year)
            month = int(month)
        except ValueError:
            return {"status": False, "error": "Неверный формат года и месяца. Ожидается YYYY-MM"}

        # Получаем информацию о студенте
        cursor.execute("SELECT id, full_name FROM students WHERE id = %s", (student_id,))
        student = cursor.fetchone()
        
        if not student:
            return {"status": False, "error": "Студент не найден"}

        # Получаем посещаемость за месяц
        start_date = f"{year}-{month:02d}-01"
        
        # Вычисляем последний день месяца
        if month == 12:
            end_date = f"{year + 1}-01-01"
        else:
            end_date = f"{year}-{month + 1:02d}-01"
        
        cursor.execute("""
            SELECT date, attendance_rate
            FROM attendance
            WHERE student_id = %s 
            AND date >= %s 
            AND date < %s
            ORDER BY date
        """, (student_id, start_date, end_date))

        attendance_records = cursor.fetchall()

        # Преобразуем в удобный формат
        attendance_data = []
        for record in attendance_records:
            attendance_data.append({
                "date": record["date"].isoformat(),
                "attendance_rate": record["attendance_rate"] if record["attendance_rate"] is not None else 1
            })

        return {
            "status": True,
            "attendance": attendance_data,
            "student": {
                "id": student["id"],
                "full_name": student["full_name"]
            }
        }

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
