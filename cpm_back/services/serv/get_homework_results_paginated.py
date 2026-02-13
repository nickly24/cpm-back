from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_homework_results_paginated(page=1, limit=10, filters=None):
    """
    Получает домашние задания с результатами с пагинацией
    Оптимизированная версия для работы с большими объемами данных
    
    Args:
        page: номер страницы (начиная с 1)
        limit: количество заданий на странице
        filters: словарь с фильтрами
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        # Базовые фильтры
        where_conditions = []
        params = []
        
        if filters:
            if filters.get('homework_type'):
                where_conditions.append("h.type = %s")
                params.append(filters['homework_type'])
            
            if filters.get('status') == 'overdue_only':
                where_conditions.append("h.deadline < CURDATE()")
            
            if filters.get('date_from'):
                where_conditions.append("h.deadline >= %s")
                params.append(filters['date_from'])
                
            if filters.get('date_to'):
                where_conditions.append("h.deadline <= %s")
                params.append(filters['date_to'])

        # Строим WHERE условие
        where_clause = ""
        if where_conditions:
            where_clause = "WHERE " + " AND ".join(where_conditions)

        # Сначала получаем только домашние задания с базовой статистикой
        homework_query = f"""
        SELECT 
            h.id as homework_id,
            h.name as homework_name,
            h.type as homework_type,
            h.deadline,
            COUNT(hs.id) as total_sessions,
            COUNT(CASE WHEN hs.status = 1 THEN 1 END) as submitted_count,
            COUNT(CASE WHEN hs.status = 0 AND h.deadline < CURDATE() THEN 1 END) as overdue_count,
            AVG(CASE WHEN hs.status = 1 THEN hs.result END) as avg_score
        FROM homework h
        LEFT JOIN homework_sessions hs ON h.id = hs.homework_id
        {where_clause}
        GROUP BY h.id, h.name, h.type, h.deadline
        ORDER BY h.deadline DESC
        LIMIT %s OFFSET %s
        """
        
        offset = (page - 1) * limit
        cursor.execute(homework_query, params + [limit, offset])
        homeworks = cursor.fetchall()

        if not homeworks:
            return {
                "status": True, 
                "res": [], 
                "pagination": {
                    "current_page": page,
                    "total_pages": 0,
                    "total_items": 0,
                    "items_per_page": limit
                }
            }

        # Получаем общее количество заданий для пагинации
        count_query = f"""
        SELECT COUNT(DISTINCT h.id) as total
        FROM homework h
        LEFT JOIN homework_sessions hs ON h.id = hs.homework_id
        {where_clause}
        """
        cursor.execute(count_query, params)
        total_items = cursor.fetchone()['total']
        total_pages = (total_items + limit - 1) // limit

        # Для каждого задания получаем детальную информацию
        result = []
        for homework in homeworks:
            homework_id = homework['homework_id']
            
            # Получаем студентов для этого задания
            students_query = """
            SELECT 
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
            FROM students s
            LEFT JOIN homework_sessions hs ON s.id = hs.student_id AND hs.homework_id = %s
            LEFT JOIN `groups` g ON s.group_id = g.id
            CROSS JOIN homework h ON h.id = %s
            ORDER BY s.full_name
            """
            
            cursor.execute(students_query, (homework_id, homework_id))
            students = cursor.fetchall()

            # Формируем результат для задания
            homework_data = {
                'homework_id': homework['homework_id'],
                'homework_name': homework['homework_name'],
                'homework_type': homework['homework_type'],
                'deadline': homework['deadline'],
                'students': students,
                'stats': {
                    'total_students': homework['total_sessions'],
                    'submitted': homework['submitted_count'],
                    'overdue': homework['overdue_count'],
                    'in_progress': homework['total_sessions'] - homework['submitted_count'] - homework['overdue_count'],
                    'not_started': 0,  # Будет рассчитано на фронтенде
                    'average_score': round(homework['avg_score'] or 0, 2),
                    'total_score': 0  # Будет рассчитано на фронтенде
                }
            }
            
            result.append(homework_data)

        return {
            "status": True,
            "res": result,
            "pagination": {
                "current_page": page,
                "total_pages": total_pages,
                "total_items": total_items,
                "items_per_page": limit
            }
        }

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": [], "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)


def get_homework_students(homework_id, page=1, limit=50, filters=None):
    """
    Получает студентов для конкретного домашнего задания с пагинацией
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        # Базовые фильтры для студентов
        where_conditions = ["h.id = %s"]
        params = [homework_id]
        
        if filters:
            if filters.get('group'):
                where_conditions.append("g.name = %s")
                params.append(filters['group'])
            
            if filters.get('status'):
                if filters['status'] == 'submitted':
                    where_conditions.append("hs.status = 1")
                elif filters['status'] == 'overdue':
                    where_conditions.append("hs.status = 0 AND h.deadline < CURDATE()")
                elif filters['status'] == 'in_progress':
                    where_conditions.append("hs.status = 0 AND h.deadline >= CURDATE()")
                elif filters['status'] == 'not_started':
                    where_conditions.append("hs.id IS NULL")

        where_clause = "WHERE " + " AND ".join(where_conditions)

        # Получаем студентов с пагинацией
        students_query = f"""
        SELECT 
            s.id as student_id,
            s.full_name as student_name,
            s.class as student_class,
            g.name as group_name,
            hs.id as session_id,
            hs.id as session_id,
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
        FROM students s
        LEFT JOIN homework_sessions hs ON s.id = hs.student_id AND hs.homework_id = %s
        LEFT JOIN `groups` g ON s.group_id = g.id
        CROSS JOIN homework h
        {where_clause}
        ORDER BY s.full_name
        LIMIT %s OFFSET %s
        """
        
        offset = (page - 1) * limit
        cursor.execute(students_query, params + [limit, offset])
        students = cursor.fetchall()

        # Получаем общее количество студентов
        count_query = f"""
        SELECT COUNT(*) as total
        FROM students s
        LEFT JOIN homework_sessions hs ON s.id = hs.student_id AND hs.homework_id = %s
        LEFT JOIN `groups` g ON s.group_id = g.id
        CROSS JOIN homework h
        {where_clause}
        """
        cursor.execute(count_query, params)
        total_items = cursor.fetchone()['total']
        total_pages = (total_items + limit - 1) // limit

        return {
            "status": True,
            "res": students,
            "pagination": {
                "current_page": page,
                "total_pages": total_pages,
                "total_items": total_items,
                "items_per_page": limit
            }
        }

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": [], "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
