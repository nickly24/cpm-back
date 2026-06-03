"""
Даты отгулов (zap_dates): нормализация, привязка к дням занятий, обработка и повтор.
"""
import re
from datetime import datetime

from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

ZAP_DATE_ROW_FIELDS = (
    'id', 'zap_id', 'date', 'status', 'class_day_id',
    'error_code', 'error_message', 'linked_at', 'last_retry_at', 'created_at',
)


def _serialize_date(value):
    if value is None:
        return None
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


def _row_to_zap_date_dict(row):
    if not row:
        return None
    if isinstance(row, dict):
        d = row
    else:
        d = dict(zip(ZAP_DATE_ROW_FIELDS, row))
    return {
        'id': d.get('id'),
        'zap_id': d.get('zap_id'),
        'date': _serialize_date(d.get('date')),
        'status': d.get('status'),
        'class_day_id': d.get('class_day_id'),
        'error_code': d.get('error_code'),
        'error_message': d.get('error_message'),
        'linked_at': _serialize_date(d.get('linked_at')) if d.get('linked_at') else None,
        'last_retry_at': _serialize_date(d.get('last_retry_at')) if d.get('last_retry_at') else None,
        'created_at': _serialize_date(d.get('created_at')) if d.get('created_at') else None,
    }


def normalize_dates(dates):
    """
    Дедупликация и валидация дат в формате YYYY-MM-DD.

    Returns:
        tuple: (normalized_list, error_message) — error_message None при успехе
    """
    if not dates or not isinstance(dates, (list, tuple)):
        return None, 'dates обязателен и должен быть непустым массивом'

    seen = set()
    result = []
    for raw in dates:
        if not isinstance(raw, str) or not DATE_RE.match(raw.strip()):
            return None, f'Недопустимая дата: {raw}'
        date_str = raw.strip()
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return None, f'Недопустимая дата: {raw}'
        if date_str not in seen:
            seen.add(date_str)
            result.append(date_str)

    if not result:
        return None, 'dates должен содержать хотя бы одну дату'

    return result, None


def insert_zap_dates(cursor, zap_id, dates):
    """Вставляет строки zap_dates со статусом pending."""
    for date_str in dates:
        cursor.execute(
            """
            INSERT INTO zap_dates (zap_id, date, status)
            VALUES (%s, %s, 'pending')
            """,
            (zap_id, date_str),
        )


def link_zap_date(cursor, zap_id, student_id, date_str):
    """
    Привязывает одну дату отгула к посещаемости (class_day_attendance, type 2).

    Returns:
        dict: status linked | no_class_day | failed и метаданные
    """
    cursor.execute(
        """
        SELECT id, status FROM zap_dates
        WHERE zap_id = %s AND date = %s
        """,
        (zap_id, date_str),
    )
    zap_date_row = cursor.fetchone()
    if not zap_date_row:
        return {
            'status': 'failed',
            'date': date_str,
            'error_code': 'no_zap_date',
            'error_message': 'Дата не найдена в запросе',
        }

    zap_date_id = zap_date_row[0]

    cursor.execute('SELECT id FROM class_days WHERE date = %s', (date_str,))
    class_day_row = cursor.fetchone()
    if not class_day_row:
        cursor.execute(
            """
            UPDATE zap_dates
            SET status = 'no_class_day',
                class_day_id = NULL,
                error_code = 'no_class_day',
                error_message = %s,
                linked_at = NULL,
                last_retry_at = NOW()
            WHERE id = %s
            """,
            ('День занятий не найден', zap_date_id),
        )
        return {
            'status': 'no_class_day',
            'date': date_str,
            'zap_date_id': zap_date_id,
            'error_code': 'no_class_day',
            'error_message': 'День занятий не найден',
        }

    class_day_id = class_day_row[0]

    try:
        cursor.execute(
            """
            INSERT INTO class_day_attendance (class_day_id, student_id, attendance_type_id, zap_id)
            VALUES (%s, %s, 2, %s)
            ON DUPLICATE KEY UPDATE
                attendance_type_id = VALUES(attendance_type_id),
                zap_id = VALUES(zap_id)
            """,
            (class_day_id, student_id, zap_id),
        )
        cursor.execute(
            """
            UPDATE zap_dates
            SET status = 'linked',
                class_day_id = %s,
                error_code = NULL,
                error_message = NULL,
                linked_at = NOW(),
                last_retry_at = NOW()
            WHERE id = %s
            """,
            (class_day_id, zap_date_id),
        )
        return {
            'status': 'linked',
            'date': date_str,
            'zap_date_id': zap_date_id,
            'class_day_id': class_day_id,
        }
    except Exception as err:
        message = str(err)[:255]
        cursor.execute(
            """
            UPDATE zap_dates
            SET status = 'failed',
                error_code = 'link_failed',
                error_message = %s,
                last_retry_at = NOW()
            WHERE id = %s
            """,
            (message, zap_date_id),
        )
        return {
            'status': 'failed',
            'date': date_str,
            'zap_date_id': zap_date_id,
            'error_code': 'link_failed',
            'error_message': message,
        }


def process_zap_dates(zap_id, student_id):
    """
    Обрабатывает все pending-даты отгула.

    Returns:
        dict: status, dates_results
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT date FROM zap_dates
            WHERE zap_id = %s AND status = 'pending'
            ORDER BY date
            """,
            (zap_id,),
        )
        pending_rows = cursor.fetchall()

        dates_results = []
        for (date_value,) in pending_rows:
            date_str = _serialize_date(date_value)
            dates_results.append(link_zap_date(cursor, zap_id, student_id, date_str))

        connection.commit()
        return {'status': True, 'dates_results': dates_results}

    except Exception as err:
        if connection:
            connection.rollback()
        return {'status': False, 'error': str(err), 'dates_results': []}

    finally:
        if connection:
            close_db_connection(connection)


def cancel_zap_dates(cursor, zap_id):
    """Отменяет все непривязанные даты отгула."""
    cursor.execute(
        """
        UPDATE zap_dates
        SET status = 'cancelled'
        WHERE zap_id = %s AND status IN ('pending', 'no_class_day', 'failed')
        """,
        (zap_id,),
    )


def retry_zap_date(zap_date_id):
    """
    Повторная привязка одной даты (отгул apr, статус no_class_day или failed).
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT zd.id, zd.zap_id, zd.date, zd.status, z.student_id, z.status AS zap_status
            FROM zap_dates zd
            JOIN zaps z ON z.id = zd.zap_id
            WHERE zd.id = %s
            """,
            (zap_date_id,),
        )
        row = cursor.fetchone()
        if not row:
            return {'status': False, 'error': 'Запись даты не найдена'}

        _, zap_id, date_value, date_status, student_id, zap_status = row

        if zap_status != 'apr':
            return {'status': False, 'error': 'Повтор возможен только для одобренного отгула'}

        if date_status not in ('no_class_day', 'failed'):
            return {'status': False, 'error': 'Повтор недоступен для текущего статуса даты'}

        date_str = _serialize_date(date_value)
        result = link_zap_date(cursor, zap_id, student_id, date_str)
        connection.commit()

        return {'status': True, 'result': result}

    except Exception as err:
        if connection:
            connection.rollback()
        return {'status': False, 'error': str(err)}

    finally:
        if connection:
            close_db_connection(connection)


def fetch_zap_dates_map(cursor, zap_ids):
    """
    Загружает даты по списку zap_id.

    Returns:
        dict: zap_id -> list of date dicts
    """
    if not zap_ids:
        return {}

    placeholders = ','.join(['%s'] * len(zap_ids))
    cursor.execute(
        f"""
        SELECT id, zap_id, date, status, class_day_id,
               error_code, error_message, linked_at, last_retry_at, created_at
        FROM zap_dates
        WHERE zap_id IN ({placeholders})
        ORDER BY zap_id, date
        """,
        tuple(zap_ids),
    )
    rows = cursor.fetchall()
    result = {zid: [] for zid in zap_ids}

    for row in rows:
        item = _row_to_zap_date_dict(row)
        result.setdefault(item['zap_id'], []).append(item)

    return result


def dates_summary_from_list(dates_list):
    """Сводка по списку дат отгула."""
    summary = {
        'total_count': len(dates_list),
        'linked_count': 0,
        'pending_count': 0,
        'no_class_day_count': 0,
        'failed_count': 0,
        'cancelled_count': 0,
    }
    for d in dates_list:
        st = d.get('status')
        if st == 'linked':
            summary['linked_count'] += 1
        elif st == 'pending':
            summary['pending_count'] += 1
        elif st == 'no_class_day':
            summary['no_class_day_count'] += 1
        elif st == 'failed':
            summary['failed_count'] += 1
        elif st == 'cancelled':
            summary['cancelled_count'] += 1
    return summary
