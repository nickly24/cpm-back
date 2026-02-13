"""
Модуль для работы с внешними тестами (tests_out) из MySQL
Эти тесты проводились вне платформы CPM-LMS
"""
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_external_tests_by_direction(direction_id):
    """
    Получает все внешние тесты по ID направления
    
    Args:
        direction_id: ID направления
        
    Returns:
        list: Список внешних тестов с информацией о результатах студента (если есть)
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        # Получаем все внешние тесты по направлению
        query = """
            SELECT 
                t.id,
                t.name,
                t.direction_id,
                t.date
            FROM tests_out t
            WHERE t.direction_id = %s
            ORDER BY t.date DESC
        """
        cursor.execute(query, (direction_id,))
        tests = cursor.fetchall()
        
        return tests
    except Exception as e:
        print(f"Ошибка при получении внешних тестов: {e}")
        return []
    finally:
        if connection:
            close_db_connection(connection)

def get_external_tests_with_results_by_student(direction_id, student_id):
    """
    Получает внешние тесты по направлению с результатами конкретного студента
    
    Args:
        direction_id: ID направления
        student_id: ID студента
        
    Returns:
        list: Список внешних тестов с результатами студента (если есть)
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        # Получаем внешние тесты с результатами студента
        query = """
            SELECT 
                t.id,
                t.name,
                t.direction_id,
                t.date,
                ts.id as session_id,
                ts.student_id,
                ts.test_id,
                ts.rate
            FROM tests_out t
            LEFT JOIN test_sessions ts ON t.id = ts.test_id AND ts.student_id = %s
            WHERE t.direction_id = %s
            ORDER BY t.date DESC
        """
        cursor.execute(query, (student_id, direction_id,))
        results = cursor.fetchall()
        
        # Форматируем результаты
        formatted_tests = []
        for row in results:
            test = {
                'id': f"external_{row['id']}",  # Префикс для идентификации внешних тестов
                'name': row['name'],
                'direction_id': row['direction_id'],
                'date': row['date'].isoformat() if row['date'] else None,
                'isExternal': True,  # Флаг, что это внешний тест
                'externalTest': True,  # Дополнительный флаг для фронтенда
                'hasResult': row['session_id'] is not None,  # Есть ли результат у студента
                'rate': row['rate'] if row['session_id'] else None,  # Результат, если есть
                'sessionId': row['session_id'] if row['session_id'] else None
            }
            formatted_tests.append(test)
        
        return formatted_tests
    except Exception as e:
        print(f"Ошибка при получении внешних тестов с результатами: {e}")
        return []
    finally:
        if connection:
            close_db_connection(connection)

def get_all_external_tests_by_direction_for_admin(direction_id):
    """
    Получает все внешние тесты по направлению для админа
    (без привязки к конкретному студенту)
    
    Args:
        direction_id: ID направления
        
    Returns:
        list: Список всех внешних тестов направления
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        query = """
            SELECT 
                t.id,
                t.name,
                t.direction_id,
                t.date
            FROM tests_out t
            WHERE t.direction_id = %s
            ORDER BY t.date DESC
        """
        cursor.execute(query, (direction_id,))
        tests = cursor.fetchall()
        
        # Форматируем результаты
        formatted_tests = []
        for row in tests:
            test = {
                'id': f"external_{row['id']}",  # Префикс для идентификации внешних тестов
                'name': row['name'],
                'direction_id': row['direction_id'],
                'date': row['date'].isoformat() if row['date'] else None,
                'isExternal': True,
                'externalTest': True
            }
            formatted_tests.append(test)
        
        return formatted_tests
    except Exception as e:
        print(f"Ошибка при получении внешних тестов для админа: {e}")
        return []
    finally:
        if connection:
            close_db_connection(connection)

