from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from .zap_dates import fetch_zap_dates_map, dates_summary_from_list, _row_to_zap_date_dict

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100


def _parse_page_limit(page, limit):
    try:
        page = int(page) if page is not None else 1
    except (TypeError, ValueError):
        page = 1
    try:
        limit = int(limit) if limit is not None else DEFAULT_PAGE_LIMIT
    except (TypeError, ValueError):
        limit = DEFAULT_PAGE_LIMIT
    page = max(1, page)
    limit = min(MAX_PAGE_LIMIT, max(1, limit))
    return page, limit, (page - 1) * limit


def _format_dates_summary_label(summary):
    """Человекочитаемая сводка по датам для списка админа."""
    total = summary.get('total_count') or 0
    if total <= 0:
        return None

    linked = summary.get('linked_count') or 0
    pending = summary.get('pending_count') or 0
    failed = (summary.get('no_class_day_count') or 0) + (summary.get('failed_count') or 0)

    if pending > 0 and linked == 0:
        return f'{total} дат, на рассмотрении'

    if linked > 0 and failed > 0:
        return f'Учтено {linked} из {total}'

    if linked > 0:
        return f'Учтено {linked} из {total}'

    if failed > 0:
        return f'Не учтено {failed} из {total}'

    return f'{total} дат'


def _attach_dates_to_zaps(zaps, dates_map, include_full_dates=True):
    for zap in zaps:
        zap_id = zap['id']
        dates_list = dates_map.get(zap_id, [])
        summary = dates_summary_from_list(dates_list)
        zap['linked_count'] = summary['linked_count']
        zap['total_count'] = summary['total_count']
        if include_full_dates:
            zap['dates'] = dates_list
        else:
            zap['dates_summary'] = summary
            zap['dates_summary_label'] = _format_dates_summary_label(summary)


def get_zaps_by_student(student_id):
    """
    Получает все запросы на отгул студента с датами и счётчиками.

    Args:
        student_id: ID студента

    Returns:
        dict: Список запросов
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                id,
                student_id,
                text,
                status,
                answer
            FROM zaps
            WHERE student_id = %s
            ORDER BY id DESC
            """,
            (student_id,),
        )

        zaps = cursor.fetchall()
        if zaps:
            zap_ids = [z['id'] for z in zaps]
            dates_map = fetch_zap_dates_map(cursor, zap_ids)
            _attach_dates_to_zaps(zaps, dates_map, include_full_dates=True)

        return {
            'status': True,
            'zaps': zaps,
        }

    except Exception as err:
        return {'status': False, 'error': str(err)}

    finally:
        if connection:
            close_db_connection(connection)


def get_all_zaps(status=None, page=1, limit=20):
    """
    Получает запросы на отгул для админов с пагинацией и сводкой по датам.

    Args:
        status: Фильтр по статусу ('set', 'apr', 'dec')
        page: Номер страницы (с 1)
        limit: Записей на страницу (по умолчанию 20)

    Returns:
        dict: Список запросов, pagination, dates_summary на каждый отгул
    """
    connection = None
    try:
        page, limit, offset = _parse_page_limit(page, limit)

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        where_parts = []
        params = []
        if status:
            where_parts.append('z.status = %s')
            params.append(status)

        where_sql = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

        count_query = f"""
            SELECT COUNT(*) AS total
            FROM zaps z
            JOIN students s ON z.student_id = s.id
            {where_sql}
        """
        cursor.execute(count_query, tuple(params))
        total_items = cursor.fetchone()['total']
        total_pages = (total_items + limit - 1) // limit if total_items else 0

        list_params = list(params) + [limit, offset]
        query = f"""
            SELECT
                z.id,
                z.student_id,
                z.text,
                z.status,
                z.answer,
                s.full_name,
                (SELECT COUNT(*) > 0 FROM zap_img zi WHERE zi.zap_id = z.id) AS has_attachments
            FROM zaps z
            JOIN students s ON z.student_id = s.id
            {where_sql}
            ORDER BY z.id DESC
            LIMIT %s OFFSET %s
        """
        cursor.execute(query, tuple(list_params))
        zaps = cursor.fetchall()

        for zap in zaps:
            zap['has_attachments'] = bool(zap.get('has_attachments'))

        if zaps:
            zap_ids = [z['id'] for z in zaps]
            dates_map = fetch_zap_dates_map(cursor, zap_ids)
            _attach_dates_to_zaps(zaps, dates_map, include_full_dates=False)

        return {
            'status': True,
            'zaps': zaps,
            'pagination': {
                'current_page': page,
                'total_pages': total_pages,
                'total_items': total_items,
                'items_per_page': limit,
            },
        }

    except Exception as err:
        return {'status': False, 'error': str(err)}

    finally:
        if connection:
            close_db_connection(connection)


def get_zap_by_id(zap_id):
    """
    Получает запрос на отгул по ID с полным списком дат.

    Args:
        zap_id: ID запроса

    Returns:
        dict: Информация о запросе с изображениями и dates
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                z.id,
                z.student_id,
                z.text,
                z.status,
                z.answer,
                s.full_name
            FROM zaps z
            JOIN students s ON z.student_id = s.id
            WHERE z.id = %s
            """,
            (zap_id,),
        )

        zap = cursor.fetchone()

        if not zap:
            return {'status': False, 'error': 'Запрос не найден'}

        cursor.execute(
            """
            SELECT id, zap_id, date, status, class_day_id,
                   error_code, error_message, linked_at, last_retry_at, created_at
            FROM zap_dates
            WHERE zap_id = %s
            ORDER BY date
            """,
            (zap_id,),
        )
        date_rows = cursor.fetchall()
        dates_list = [_row_to_zap_date_dict(r) for r in date_rows]
        summary = dates_summary_from_list(dates_list)
        zap['dates'] = dates_list
        zap['linked_count'] = summary['linked_count']
        zap['total_count'] = summary['total_count']

        cursor.execute('SELECT id, img, type FROM zap_img WHERE zap_id = %s', (zap_id,))
        images = cursor.fetchall()

        return {
            'status': True,
            'zap': zap,
            'images': images,
        }

    except Exception as err:
        return {'status': False, 'error': str(err)}

    finally:
        if connection:
            close_db_connection(connection)
