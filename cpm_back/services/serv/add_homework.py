from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
import datetime


def create_homework_and_sessions(
    homework_name, homework_type, deadline_str, published=True
):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        deadline = datetime.datetime.strptime(deadline_str, "%Y-%m-%d").date()

        published_val = 1 if published else 0
        cursor.execute(
            "INSERT INTO homework (name, type, deadline, published) VALUES (%s, %s, %s, %s)",
            (homework_name, homework_type, deadline, published_val),
        )
        homework_id = cursor.lastrowid

        cursor.execute(
            """
            INSERT INTO homework_sessions (status, result, homework_id, student_id)
            SELECT 0, 0, %s, id FROM students
            """,
            (homework_id,),
        )
        sessions_created = cursor.rowcount
        connection.commit()

        return {
            "status": True,
            "homeworkId": homework_id,
            "sessionsCreated": sessions_created,
        }

    except ValueError:
        return {"status": False, "error": "Неверный формат даты"}
    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
