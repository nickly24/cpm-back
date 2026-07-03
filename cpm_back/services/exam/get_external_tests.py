"""
Модуль для работы с внешними тестами (tests_out) из MySQL
Эти тесты проводились вне платформы CPM-LMS
"""
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection


def _format_external_test(row):
    return {
        'id': f"external_{row['id']}",
        'numeric_id': row['id'],
        'name': row['name'],
        'direction_id': row['direction_id'],
        'direction_name': row.get('direction_name'),
        'date': row['date'].isoformat() if row.get('date') else None,
        'isExternal': True,
        'externalTest': True,
    }


def create_external_test(name, direction_id, date):
    """
    Создает внешний тест в MySQL tests_out.

    Внешний тест не содержит вопросов и не запускается в CPM-LMS: он нужен для
    отображения факта внешней сдачи и дальнейшей привязки результатов.
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            INSERT INTO tests_out (name, direction_id, date)
            VALUES (%s, %s, %s)
            """,
            (name, direction_id, date),
        )
        test_id = cursor.lastrowid
        connection.commit()
        return {
            'success': True,
            'test': {
                'id': f"external_{test_id}",
                'numeric_id': test_id,
                'name': name,
                'direction_id': direction_id,
                'date': date.isoformat() if hasattr(date, 'isoformat') else str(date),
                'isExternal': True,
                'externalTest': True,
            },
        }
    except Exception as e:
        print(f"Ошибка при создании внешнего теста: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        if connection:
            close_db_connection(connection)


def parse_external_test_id(test_id):
    text = str(test_id or "").strip()
    if text.startswith("external_"):
        text = text[len("external_"):]
    try:
        parsed = int(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def get_external_test_by_id(test_id):
    numeric_id = parse_external_test_id(test_id)
    if not numeric_id:
        return None

    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                t.id,
                t.name,
                t.direction_id,
                t.date,
                d.name AS direction_name
            FROM tests_out t
            LEFT JOIN directions d ON d.id = t.direction_id
            WHERE t.id = %s
            """,
            (numeric_id,),
        )
        row = cursor.fetchone()
        return _format_external_test(row) if row else None
    except Exception as e:
        print(f"Ошибка при получении внешнего теста: {e}")
        return None
    finally:
        if connection:
            close_db_connection(connection)


def get_all_external_tests_for_admin():
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                t.id,
                t.name,
                t.direction_id,
                t.date,
                d.name AS direction_name
            FROM tests_out t
            LEFT JOIN directions d ON d.id = t.direction_id
            ORDER BY t.date DESC, t.id DESC
            """
        )
        return [_format_external_test(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Ошибка при получении внешних тестов: {e}")
        return []
    finally:
        if connection:
            close_db_connection(connection)


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
                t.date,
                d.name AS direction_name
            FROM tests_out t
            LEFT JOIN directions d ON d.id = t.direction_id
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
                d.name AS direction_name,
                ts.id as session_id,
                ts.student_id,
                ts.test_id,
                ts.rate
            FROM tests_out t
            LEFT JOIN directions d ON d.id = t.direction_id
            LEFT JOIN test_sessions ts ON t.id = ts.test_id AND ts.student_id = %s
            WHERE t.direction_id = %s
            ORDER BY t.date DESC
        """
        cursor.execute(query, (student_id, direction_id,))
        results = cursor.fetchall()
        
        formatted_tests = []
        for row in results:
            test = {
                **_format_external_test(row),
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
                t.date,
                d.name AS direction_name
            FROM tests_out t
            LEFT JOIN directions d ON d.id = t.direction_id
            WHERE t.direction_id = %s
            ORDER BY t.date DESC
        """
        cursor.execute(query, (direction_id,))
        tests = cursor.fetchall()
        
        formatted_tests = []
        for row in tests:
            formatted_tests.append(_format_external_test(row))
        
        return formatted_tests
    except Exception as e:
        print(f"Ошибка при получении внешних тестов для админа: {e}")
        return []
    finally:
        if connection:
            close_db_connection(connection)


def count_external_test_results(test_id):
    numeric_id = parse_external_test_id(test_id)
    if not numeric_id:
        return 0

    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM test_sessions WHERE test_id = %s",
            (numeric_id,),
        )
        row = cursor.fetchone()
        return int(row["cnt"]) if row else 0
    except Exception as e:
        print(f"Ошибка при подсчёте результатов внешнего теста: {e}")
        return 0
    finally:
        if connection:
            close_db_connection(connection)


def get_external_test_delete_preview(test_id):
    test = get_external_test_by_id(test_id)
    if not test:
        return None
    return {
        "test": test,
        "resultsCount": count_external_test_results(test_id),
    }


def delete_external_test(test_id):
    numeric_id = parse_external_test_id(test_id)
    if not numeric_id:
        return None

    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT id FROM tests_out WHERE id = %s", (numeric_id,))
        if not cursor.fetchone():
            return None

        cursor.execute(
            "DELETE FROM test_sessions WHERE test_id = %s",
            (numeric_id,),
        )
        results_deleted = cursor.rowcount

        cursor.execute(
            "DELETE FROM tests_out WHERE id = %s",
            (numeric_id,),
        )
        test_deleted = cursor.rowcount > 0
        connection.commit()

        return {
            "testDeleted": test_deleted,
            "resultsDeleted": results_deleted,
        }
    except Exception as e:
        if connection:
            connection.rollback()
        print(f"Ошибка при удалении внешнего теста: {e}")
        raise
    finally:
        if connection:
            close_db_connection(connection)
