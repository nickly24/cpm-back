from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from .zap_dates import normalize_dates, insert_zap_dates


def create_zap(student_id, text, dates, images=None):
    """
    Создает новый запрос на отгул от студента

    Args:
        student_id: ID студента
        text: Текст запроса
        dates: Список дат YYYY-MM-DD (минимум 1)
        images: Список словарей с blob данных и типом файла [{"data": blob, "type": "image/jpeg"}, ...]

    Returns:
        dict: Результат создания с zap_id
    """
    normalized_dates, dates_error = normalize_dates(dates)
    if dates_error:
        return {'status': False, 'error': dates_error}

    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute('SELECT id, full_name FROM students WHERE id = %s', (student_id,))
        student = cursor.fetchone()
        if not student:
            return {'status': False, 'error': 'Студент не найден'}

        cursor.execute(
            "INSERT INTO zaps (student_id, text, status) VALUES (%s, %s, 'set')",
            (student_id, text),
        )
        zap_id = cursor.lastrowid

        insert_zap_dates(cursor, zap_id, normalized_dates)

        if images:
            for img_data in images:
                img_blob = img_data.get('data')
                img_type = img_data.get('type', 'image/jpeg')
                cursor.execute(
                    'INSERT INTO zap_img (zap_id, img, type) VALUES (%s, %s, %s)',
                    (zap_id, img_blob, img_type),
                )

        connection.commit()

        return {
            'status': True,
            'zap_id': zap_id,
            'dates': normalized_dates,
            'message': 'Запрос успешно создан',
        }

    except Exception as err:
        if connection:
            connection.rollback()
        return {'status': False, 'error': str(err)}

    finally:
        if connection:
            close_db_connection(connection)
