from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

from .school_schema import require_schools_schema


def get_student_ids_and_names_by_school(school_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        schema_err = require_schools_schema(cursor)
        if schema_err:
            return schema_err

        cursor.execute(
            """
            SELECT id, full_name, class, group_id, school_id
            FROM students
            WHERE school_id = %s
            ORDER BY full_name ASC
            """,
            (school_id,),
        )
        results = cursor.fetchall()

        if not results:
            return {"status": False, "res": []}

        data = [
            {
                "id": row["id"],
                "full_name": row["full_name"],
                "class": row["class"],
                "group_id": row["group_id"],
                "school_id": row["school_id"],
            }
            for row in results
        ]
        return {"status": True, "res": data}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": [], "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)


def get_unassigned_students_by_school():
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        schema_err = require_schools_schema(cursor)
        if schema_err:
            return schema_err

        cursor.execute(
            """
            SELECT id, full_name, class, group_id
            FROM students
            WHERE school_id IS NULL
            ORDER BY full_name ASC
            """
        )
        students = cursor.fetchall()

        return {
            "status": True,
            "unassigned_students": [
                {
                    "student_id": row["id"],
                    "full_name": row["full_name"],
                    "class": row["class"],
                    "group_id": row["group_id"],
                }
                for row in students
            ],
        }

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "unassigned_students": [], "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
