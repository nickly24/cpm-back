"""
Модуль для получения таблицы данных по домашним заданиям типов ОВ и ДЗНВ
Возвращает данные в формате: студенты и их результаты по всем домашкам
"""

from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
import datetime


def get_ov_homework_table():
    """
    Получает таблицу данных по домашним заданиям типов ОВ и ДЗНВ
    Возвращает данные в формате:
    {
        "homeworks": [
            {
                "id": 1,
                "name": "ДЗ 1",
                "deadline": "2024-01-15",
                ...
            }
        ],
        "students": [
            {
                "id": 1,
                "full_name": "Иванов Иван",
                "class": 10,
                "group_name": "Группа А",
                "results": [
                    {
                        "homework_id": 1,
                        "status": 1,
                        "result": 85.5,
                        "date_pass": "2024-01-14",
                        "status_text": "Сдано"
                    },
                    ...
                ]
            }
        ]
    }
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        # Получаем все домашние задания типов ОВ и ДЗНВ, отсортированные по дедлайну (новые сначала)
        homework_query = """
            SELECT 
                h.id,
                h.name,
                h.type,
                h.deadline
            FROM homework h
            WHERE h.type IN ('ОВ', 'ДЗНВ')
            ORDER BY h.deadline DESC
        """
        
        cursor.execute(homework_query)
        homeworks = cursor.fetchall()

        if not homeworks:
            return {
                "status": True,
                "homeworks": [],
                "students": []
            }

        # Получаем всех студентов с их группами
        students_query = """
            SELECT 
                s.id,
                s.full_name,
                s.class,
                g.name as group_name
            FROM students s
            LEFT JOIN `groups` g ON s.group_id = g.id
            ORDER BY s.full_name ASC
        """
        
        cursor.execute(students_query)
        students = cursor.fetchall()

        if not students:
            return {
                "status": True,
                "homeworks": homeworks,
                "students": []
            }

        # Для каждого студента получаем результаты по всем ОВ домашкам
        homework_ids = [hw['id'] for hw in homeworks]
        
        if homework_ids:
            # Создаем плейсхолдеры для IN запроса
            placeholders = ','.join(['%s'] * len(homework_ids))
            
            for student in students:
                student_id = student['id']
                results = []
                
                # Получаем результаты студента по всем ОВ домашкам
                results_query = f"""
                    SELECT 
                        h.id as homework_id,
                        hs.status,
                        hs.result,
                        hs.date_pass,
                        h.deadline,
                        CASE 
                            WHEN hs.status = 1 THEN 'Сдано'
                            WHEN hs.status = 0 AND h.deadline < CURDATE() THEN 'Просрочено'
                            WHEN hs.status = 0 AND h.deadline >= CURDATE() THEN 'В процессе'
                            ELSE 'Не начато'
                        END as status_text,
                        CASE 
                            WHEN hs.date_pass IS NOT NULL AND hs.date_pass > h.deadline THEN 
                                DATEDIFF(hs.date_pass, h.deadline)
                            ELSE 0
                        END as days_overdue
                    FROM homework h
                    LEFT JOIN homework_sessions hs ON h.id = hs.homework_id AND hs.student_id = %s
                    WHERE h.type IN ('ОВ', 'ДЗНВ') AND h.id IN ({placeholders})
                    ORDER BY h.deadline DESC
                """
                
                cursor.execute(results_query, [student_id] + homework_ids)
                student_results = cursor.fetchall()
                
                # Формируем результаты в правильном порядке (по порядку домашних заданий)
                results_dict = {r['homework_id']: r for r in student_results if r['homework_id']}
                
                for hw in homeworks:
                    hw_id = hw['id']
                    if hw_id in results_dict:
                        result = results_dict[hw_id]
                        results.append({
                            "homework_id": hw_id,
                            "status": result['status'] if result['status'] is not None else 0,
                            "result": float(result['result']) if result['result'] is not None else None,
                            "date_pass": str(result['date_pass']) if result['date_pass'] else None,
                            "deadline": str(result['deadline']) if result['deadline'] else None,
                            "status_text": result['status_text'],
                            "days_overdue": result['days_overdue'] if result['days_overdue'] is not None else 0
                        })
                    else:
                        # Если нет записи, определяем статус на основе дедлайна
                        deadline = hw['deadline']
                        if deadline:
                            deadline_date = deadline
                            if isinstance(deadline_date, str):
                                deadline_date = datetime.datetime.strptime(deadline_date, '%Y-%m-%d').date()
                            today = datetime.date.today()
                            if deadline_date < today:
                                status_text = 'Просрочено'
                            else:
                                status_text = 'В процессе'
                        else:
                            status_text = 'Не начато'
                        
                        results.append({
                            "homework_id": hw_id,
                            "status": 0,
                            "result": None,
                            "date_pass": None,
                            "deadline": str(hw['deadline']) if hw['deadline'] else None,
                            "status_text": status_text,
                            "days_overdue": 0
                        })
                
                student['results'] = results
        else:
            # Если нет домашних заданий, добавляем пустые результаты
            for student in students:
                student['results'] = []

        # Преобразуем даты в строки для JSON
        for hw in homeworks:
            if hw['deadline']:
                hw['deadline'] = str(hw['deadline'])

        return {
            "status": True,
            "homeworks": homeworks,
            "students": students
        }

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {
            "status": False,
            "error": str(err),
            "homeworks": [],
            "students": []
        }

    finally:
        if connection:
            close_db_connection(connection)

