from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from cpm_back.services.serv.student_plain_credentials import (
    ensure_student_credentials_table,
    serialize_student_credentials,
    update_student_credentials,
)
from werkzeug.security import generate_password_hash

_UNSET = object()


def edit_student(
    student_id,
    full_name=None,
    class_number=None,
    group_id=None,
    tg_name=None,
    school_id=_UNSET,
    login=None,
    password=None,
):
    """
    Редактирует данные студента
    
    Args:
        student_id (int): ID студента
        full_name (str, optional): Новое полное имя студента
        class_number (int, optional): Новый класс студента
        group_id (int, optional): Новый ID группы студента
        school_id (int, optional): Новый ID школы (null — снять привязку)
        tg_name (str, optional): Новый Telegram никнейм студента
        login (str, optional): Новый логин ученика
        password (str, optional): Новый пароль ученика
    
    Returns:
        dict: Результат операции с обновленными данными студента
    """
    connection = None
    try:
        # Проверяем, что хотя бы одно поле для обновления передано
        if all(
            param is None
            for param in [full_name, class_number, group_id, tg_name, login, password]
        ) and school_id is _UNSET:
            return {
                "status": False,
                "error": "Необходимо указать хотя бы одно поле для обновления"
            }
        
        # Проверяем корректность класса, если он передан
        if class_number is not None and class_number <= 0:
            return {
                "status": False,
                "error": "Класс должен быть положительным целым числом"
            }

        if login is not None:
            login = str(login).strip()
            if not login:
                return {
                    "status": False,
                    "error": "Логин не может быть пустым"
                }

        if password is not None:
            password = str(password).strip()
            if not password:
                return {
                    "status": False,
                    "error": "Пароль не может быть пустым"
                }
        
        # Получаем подключение из пула
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        ensure_student_credentials_table(cursor)
        
        # Проверяем существование студента
        cursor.execute("SELECT * FROM students WHERE id = %s", (student_id,))
        student = cursor.fetchone()
        
        if not student:
            return {
                "status": False,
                "error": f"Студент с ID {student_id} не найден"
            }

        if login is not None:
            cursor.execute(
                """
                SELECT 1
                FROM auth_users
                WHERE username = %s
                  AND NOT (role = 'student' AND ref_id = %s)
                LIMIT 1
                """,
                (login, student_id),
            )
            if cursor.fetchone():
                return {
                    "status": False,
                    "error": "Такой логин уже занят"
                }
        
        # Формируем список полей для обновления
        update_fields = []
        update_values = []
        
        if full_name is not None:
            update_fields.append("full_name = %s")
            update_values.append(full_name)
        
        if class_number is not None:
            update_fields.append("class = %s")
            update_values.append(class_number)
        
        if group_id is not None:
            update_fields.append("group_id = %s")
            update_values.append(group_id)

        if school_id is not _UNSET:
            from .school_schema import is_schools_schema_ready

            if not is_schools_schema_ready(cursor):
                from .school_schema import schools_schema_error
                return schools_schema_error()

            if school_id is not None:
                from .school_utils import validate_school_id

                school_check = validate_school_id(cursor, school_id)
                if not school_check["status"]:
                    return {"status": False, "error": school_check["error"]}
            update_fields.append("school_id = %s")
            update_values.append(school_id)

        if tg_name is not None:
            update_fields.append("tg_name = %s")
            update_values.append(tg_name)
        
        if update_fields:
            # Добавляем student_id в конец списка значений
            update_values.append(student_id)

            update_query = f"""
            UPDATE students
            SET {', '.join(update_fields)}
            WHERE id = %s
            """
            cursor.execute(update_query, tuple(update_values))

        if login is not None or password is not None:
            auth_fields = []
            auth_values = []
            if login is not None:
                auth_fields.append("username = %s")
                auth_values.append(login)
            if password is not None:
                auth_fields.append("password = %s")
                auth_values.append(generate_password_hash(password))

            if auth_fields:
                auth_values.extend([student_id, "student"])
                cursor.execute(
                    f"""
                    UPDATE auth_users
                    SET {', '.join(auth_fields)}
                    WHERE ref_id = %s AND role = %s
                    """,
                    tuple(auth_values),
                )

            update_student_credentials(
                cursor,
                student_id,
                login=login,
                password=password,
            )
        
        # Подтверждаем транзакцию
        connection.commit()
        
        # Получаем обновленные данные студента
        cursor.execute(
            """
            SELECT
                s.*,
                a.username AS auth_login,
                sc.login AS credential_login,
                sc.password AS credential_password
            FROM students s
            LEFT JOIN auth_users a ON a.ref_id = s.id AND a.role = 'student'
            LEFT JOIN student_credentials sc ON sc.student_id = s.id
            WHERE s.id = %s
            """,
            (student_id,),
        )
        updated_student = cursor.fetchone()
        credentials = serialize_student_credentials(updated_student)
        
        return {
            "status": True,
            "message": "Данные студента успешно обновлены",
            "student_data": {
                "student_id": updated_student['id'],
                "full_name": updated_student['full_name'],
                "class": updated_student['class'],
                "group_id": updated_student['group_id'],
                "school_id": updated_student.get('school_id'),
                "tg_name": updated_student.get('tg_name'),
                "login": credentials["login"],
                "password": credentials["password"],
                "password_hidden": credentials["password_hidden"],
            }
        }
        
    except Exception as e:
        if connection:
            connection.rollback()
        return {
            "status": False,
            "error": f"Ошибка базы данных: {str(e)}"
        }
        
    finally:
        if connection:
            close_db_connection(connection)
