from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_all_homework_results():
    """
    Получает все домашние задания с результатами всех студентов
    Возвращает данные в формате, удобном для админки
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        # Получаем все домашние задания с результатами студентов
        query = """
        SELECT 
            h.id as homework_id,
            h.name as homework_name,
            h.type as homework_type,
            h.deadline,
            s.id as student_id,
            s.full_name as student_name,
            s.class as student_class,
            g.name as group_name,
            hs.status,
            hs.result,
            hs.date_pass,
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
        CROSS JOIN students s
        LEFT JOIN homework_sessions hs ON h.id = hs.homework_id AND s.id = hs.student_id
        LEFT JOIN groups g ON s.group_id = g.id
        ORDER BY h.deadline DESC, s.full_name ASC
        """
        
        cursor.execute(query)
        results = cursor.fetchall()

        if not results:
            return {"status": False, "res": []}

        # Группируем данные по домашним заданиям
        homework_data = {}
        for row in results:
            homework_id = row['homework_id']
            
            if homework_id not in homework_data:
                homework_data[homework_id] = {
                    'homework_id': homework_id,
                    'homework_name': row['homework_name'],
                    'homework_type': row['homework_type'],
                    'deadline': row['deadline'],
                    'students': [],
                    'stats': {
                        'total_students': 0,
                        'submitted': 0,
                        'overdue': 0,
                        'in_progress': 0,
                        'not_started': 0,
                        'average_score': 0,
                        'total_score': 0
                    }
                }
            
            # Добавляем студента к заданию
            student_data = {
                'student_id': row['student_id'],
                'student_name': row['student_name'],
                'student_class': row['student_class'],
                'group_name': row['group_name'],
                'status': row['status'],
                'result': row['result'],
                'date_pass': row['date_pass'],
                'status_text': row['status_text'],
                'days_overdue': row['days_overdue']
            }
            
            homework_data[homework_id]['students'].append(student_data)
            
            # Обновляем статистику
            stats = homework_data[homework_id]['stats']
            stats['total_students'] += 1
            
            if row['status'] == 1:
                stats['submitted'] += 1
                if row['result'] is not None:
                    stats['total_score'] += row['result']
            elif row['status_text'] == 'Просрочено':
                stats['overdue'] += 1
            elif row['status_text'] == 'В процессе':
                stats['in_progress'] += 1
            else:
                stats['not_started'] += 1
        
        # Вычисляем средние баллы
        for homework_id in homework_data:
            stats = homework_data[homework_id]['stats']
            if stats['submitted'] > 0:
                stats['average_score'] = round(stats['total_score'] / stats['submitted'], 2)
        
        # Преобразуем в список
        homework_list = list(homework_data.values())
        
        return {"status": True, "res": homework_list}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": []}

    finally:
        if connection:
            close_db_connection(connection)
