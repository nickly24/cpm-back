from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

from .school_schema import is_schools_schema_ready
from .student_plain_credentials import ensure_student_credentials_table, serialize_student_credentials


def _serialize_student_row(student):
    payload = {
        "student_id": student["id"],
        "full_name": student["full_name"],
        "group_id": student["group_id"],
        "class": student["class"],
        "tg_name": student["tg_name"],
    }
    if "school_id" in student:
        payload["school_id"] = student.get("school_id")
        payload["school_name"] = student.get("school_name")
        payload["school_short_name"] = student.get("school_short_name")
    else:
        payload["school_id"] = None
        payload["school_name"] = None
        payload["school_short_name"] = None
    payload.update(serialize_student_credentials(student))
    return payload


def get_all_students():
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        ensure_student_credentials_table(cursor)

        if is_schools_schema_ready(cursor):
            cursor.execute("""
                SELECT
                    s.id,
                    s.full_name,
                    s.group_id,
                    s.school_id,
                    s.class,
                    s.tg_name,
                    a.username AS auth_login,
                    sc.login AS credential_login,
                    sc.password AS credential_password,
                    sch.name AS school_name,
                    sch.short_name AS school_short_name
                FROM students s
                LEFT JOIN auth_users a ON a.ref_id = s.id AND a.role = 'student'
                LEFT JOIN student_credentials sc ON sc.student_id = s.id
                LEFT JOIN schools sch ON sch.id = s.school_id
                ORDER BY s.full_name ASC
            """)
        else:
            cursor.execute("""
                SELECT
                    s.id,
                    s.full_name,
                    s.group_id,
                    s.class,
                    s.tg_name,
                    a.username AS auth_login,
                    sc.login AS credential_login,
                    sc.password AS credential_password
                FROM students s
                LEFT JOIN auth_users a ON a.ref_id = s.id AND a.role = 'student'
                LEFT JOIN student_credentials sc ON sc.student_id = s.id
                ORDER BY s.full_name ASC
            """)

        students = cursor.fetchall()
        result = [_serialize_student_row(student) for student in students]
        return {"status": True, "res": result}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": [], "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
