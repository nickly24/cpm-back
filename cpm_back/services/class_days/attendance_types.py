"""
Справочник типов посещения (таблица attendance_types).
"""
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection


def get_all_attendance_types():
    """
    Возвращает все типы посещения (1–8) для отображения в формах и отчётах.
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, code, name_ru, sort_order
            FROM attendance_types
            ORDER BY sort_order, id
        """)
        rows = cursor.fetchall()
        return {
            "status": True,
            "types": [
                {
                    "id": r["id"],
                    "code": r["code"],
                    "name_ru": r["name_ru"],
                    "sort_order": r["sort_order"],
                }
                for r in rows
            ],
        }
    except Exception as err:
        return {"status": False, "error": str(err)}
    finally:
        if connection:
            close_db_connection(connection)
