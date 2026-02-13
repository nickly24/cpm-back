from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
import calendar
import datetime

def get_attendance_diary(year: str, month: str):
    try:
        year_int = int(year)
        month_int = int(month)
    except ValueError:
        return {'status': False, 'error': 'Год и месяц должны быть числами'}

    if month_int < 1 or month_int > 12:
        return {'status': False, 'error': 'Месяц должен быть от 1 до 12'}

    _, num_days = calendar.monthrange(year_int, month_int)

    # Генерируем все даты месяца
    days_list = []
    for day in range(1, num_days + 1):
        weekday = calendar.weekday(year_int, month_int, day)
        days_list.append({'day': day, 'weekday': weekday})

    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        # Получаем всех студентов
        cursor.execute("SELECT id, full_name FROM students ORDER BY full_name ASC")
        students = cursor.fetchall()

        students_dict = {s['id']: s['full_name'] for s in students}

        # Получаем всю посещаемость за месяц одним запросом
        start_date = f"{year_int}-{month_int:02d}-01"
        end_date = f"{year_int}-{month_int:02d}-{num_days:02d}"

        cursor.execute("""
            SELECT date, student_id, attendance_rate
            FROM attendance
            WHERE date BETWEEN %s AND %s
        """, (start_date, end_date))

        attendance_raw = cursor.fetchall()

        # Собираем посещаемость в структуру
        attendance_map = {}
        for row in attendance_raw:
            date_str = row['date'].strftime('%Y-%m-%d')
            student_id = row['student_id']
            attendance_rate = row['attendance_rate']
            
            if date_str not in attendance_map:
                attendance_map[date_str] = {}
            
            # Храним attendance_rate для каждого студента
            attendance_map[date_str][student_id] = attendance_rate

        # Формируем итоговый отчет
        students_report = []
        for student_id, full_name in students_dict.items():
            attendance_marks = []
            for day_info in days_list:
                day = day_info['day']
                weekday = day_info['weekday']
                date_str = f"{year_int}-{month_int:02d}-{day:02d}"

                if weekday >= 5:
                    attendance_marks.append("")
                elif date_str in attendance_map and student_id in attendance_map[date_str]:
                    # Если есть запись, проверяем attendance_rate
                    rate = attendance_map[date_str][student_id]
                    if rate == 2:
                        attendance_marks.append("✓")  # Уважительная причина
                    else:
                        attendance_marks.append("+")  # Очная посещаемость
                else:
                    attendance_marks.append("-")  # Отсутствовал

            students_report.append({
                'student_id': student_id,
                'full_name': full_name,
                'attendance': attendance_marks
            })

        weekday_names = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
        formatted_days = [
            {'day': d['day'], 'weekday': weekday_names[d['weekday']]}
            for d in days_list
        ]

        return {'status': True, 'res': {'days': formatted_days, 'students': students_report}}

    except Exception as err:
        return {'status': False, 'error': str(err)}

    finally:
        if connection:
            close_db_connection(connection)