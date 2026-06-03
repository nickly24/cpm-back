"""
Проверка наличия таблицы schools и колонки students.school_id (миграция 004).
"""

_schema_ready = None


def is_schools_schema_ready(cursor, *, force_refresh=False):
    global _schema_ready
    if _schema_ready is None or force_refresh:
        cursor.execute("SHOW TABLES LIKE 'schools'")
        if not cursor.fetchone():
            _schema_ready = False
        else:
            cursor.execute("SHOW COLUMNS FROM students LIKE 'school_id'")
            _schema_ready = cursor.fetchone() is not None
    return _schema_ready


def schools_schema_error():
    return {
        "status": False,
        "error": (
            "Схема школ не применена на сервере БД. "
            "Выполните migrations/004_schools.sql"
        ),
        "code": "schools_schema_missing",
    }


def require_schools_schema(cursor):
    if is_schools_schema_ready(cursor):
        return None
    return schools_schema_error()
