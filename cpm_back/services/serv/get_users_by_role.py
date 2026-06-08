from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

from .school_schema import is_schools_schema_ready
from .staff_credentials import expose_password


def _staff_row(row, include_group=False):
    item = {
        "id": row["id"],
        "full_name": row["full_name"],
        "login": row.get("login"),
        "password": expose_password(row.get("password_raw")),
        "password_hidden": bool(row.get("password_raw")) and expose_password(row.get("password_raw")) is None,
    }
    if include_group:
        item["group_id"] = row.get("group_id")
        item["group_name"] = row.get("group_name")
    return item


def get_users_by_role(role):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if role == "student":
            if is_schools_schema_ready(cursor):
                cursor.execute("""
                    SELECT
                        s.id,
                        s.full_name,
                        s.group_id,
                        s.school_id,
                        s.class,
                        s.tg_name,
                        sch.name AS school_name,
                        sch.short_name AS school_short_name
                    FROM students s
                    LEFT JOIN schools sch ON sch.id = s.school_id
                    ORDER BY s.full_name
                """)
            else:
                cursor.execute(
                    "SELECT id, full_name, group_id, class, tg_name FROM students ORDER BY full_name"
                )
            result = [{
                "id": row["id"],
                "full_name": row["full_name"],
                "group_id": row["group_id"],
                "school_id": row.get("school_id"),
                "school_name": row.get("school_name"),
                "school_short_name": row.get("school_short_name"),
                "class": row["class"],
                "tg_name": row["tg_name"]
            } for row in cursor.fetchall()]
        elif role == "proctor":
            cursor.execute("""
                SELECT
                    p.id,
                    p.full_name,
                    p.group_id,
                    g.name AS group_name,
                    a.username AS login,
                    a.password AS password_raw
                FROM proctors p
                LEFT JOIN `groups` g ON g.id = p.group_id
                LEFT JOIN auth_users a ON a.ref_id = p.id AND a.role = 'proctor'
                ORDER BY p.full_name
            """)
            result = [_staff_row(row, include_group=True) for row in cursor.fetchall()]
        elif role in ("examinator", "supervisor"):
            table = "examinators" if role == "examinator" else "supervisors"
            cursor.execute(
                f"""
                SELECT
                    u.id,
                    u.full_name,
                    a.username AS login,
                    a.password AS password_raw
                FROM {table} u
                LEFT JOIN auth_users a ON a.ref_id = u.id AND a.role = %s
                ORDER BY u.full_name
                """,
                (role,),
            )
            result = [_staff_row(row) for row in cursor.fetchall()]
        else:
            return {"status": False, "error": "Invalid role provided."}

        return {"status": True, "res": result}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": [], "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
