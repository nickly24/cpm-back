from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from .zap_dates import process_zap_dates, cancel_zap_dates


def process_zap(zap_id, status, answer):
    """
    Обрабатывает запрос на отгул (одобрить или отклонить).
    При одобрении привязывает даты из zap_dates к посещаемости.
    При отклонении отменяет непривязанные даты.

    Args:
        zap_id: ID запроса
        status: Новый статус ('apr' или 'dec')
        answer: Ответ админа

    Returns:
        dict: Результат обработки с dates_results при одобрении
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            'SELECT id, student_id, status FROM zaps WHERE id = %s',
            (zap_id,),
        )

        zap = cursor.fetchone()
        if not zap:
            return {'status': False, 'error': 'Запрос не найден'}

        student_id = zap[1]

        cursor.execute(
            """
            UPDATE zaps
            SET status = %s, answer = %s
            WHERE id = %s
            """,
            (status, answer, zap_id),
        )

        dates_results = []

        if status == 'dec':
            cancel_zap_dates(cursor, zap_id)
            connection.commit()
            return {
                'status': True,
                'message': 'Запрос отклонён, даты отменены',
                'dates_results': dates_results,
            }

        if status == 'apr':
            connection.commit()
            close_db_connection(connection)
            connection = None

            process_result = process_zap_dates(zap_id, student_id)
            if not process_result.get('status'):
                return {
                    'status': False,
                    'error': process_result.get('error', 'Ошибка привязки дат'),
                    'dates_results': process_result.get('dates_results', []),
                }

            dates_results = process_result.get('dates_results', [])
            linked = sum(1 for r in dates_results if r.get('status') == 'linked')
            total = len(dates_results)

            return {
                'status': True,
                'message': f'Запрос одобрен, привязано {linked} из {total} дат',
                'dates_results': dates_results,
            }

        connection.commit()
        return {
            'status': True,
            'message': 'Запрос успешно обработан',
            'dates_results': dates_results,
        }

    except Exception as err:
        if connection:
            connection.rollback()
        return {'status': False, 'error': str(err)}

    finally:
        if connection:
            close_db_connection(connection)
