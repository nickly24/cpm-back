from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from cpm_back.services.serv.student_credentials import generate_student_login
from cpm_back.services.serv.student_plain_credentials import upsert_student_credentials
from werkzeug.security import generate_password_hash
import random
import string

def add_student(full_name, class_number, tg_name=None, school_id=None):
    """
    Добавляет нового студента с автоматической генерацией логина и пароля
    
    Args:
        full_name (str): Полное имя студента
        class_number (int): Класс студента
        tg_name (str, optional): Telegram никнейм студента
        school_id (int, optional): ID школы
    
    Returns:
        dict: Результат операции с данными студента
    """
    connection = None
    try:
        # Проверяем корректность класса
        if class_number is None or class_number <= 0:
            return {
                "status": False,
                "error": "Класс должен быть положительным целым числом"
            }
        
        # Получаем подключение из пула
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if school_id is not None:
            from .school_schema import is_schools_schema_ready
            from .school_utils import validate_school_id

            if not is_schools_schema_ready(cursor):
                from .school_schema import schools_schema_error
                return schools_schema_error()

            school_check = validate_school_id(cursor, school_id)
            if not school_check["status"]:
                return {"status": False, "error": school_check["error"]}
        
        # Генерируем логин на основе ФИО: 4 буквы фамилии + 2 имени + 3 цифры
        login = generate_student_login(cursor, full_name)
        if not login:
            return {
                "status": False,
                "error": "Необходимо указать имя и фамилию"
            }
        
        # tg_name в БД NOT NULL — пустая строка, если не передан
        if tg_name is None:
            tg_name = ""
        else:
            tg_name = str(tg_name).strip()

        # Генерируем пароль (8 символов: буквы + цифры)
        password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        
        # 1. Добавляем студента в таблицу students
        from .school_schema import is_schools_schema_ready

        if is_schools_schema_ready(cursor):
            insert_student_query = """
            INSERT INTO students (full_name, class, group_id, school_id, tg_name) 
            VALUES (%s, %s, NULL, %s, %s)
            """
            cursor.execute(insert_student_query, (full_name, class_number, school_id, tg_name))
        else:
            insert_student_query = """
            INSERT INTO students (full_name, class, group_id, tg_name) 
            VALUES (%s, %s, NULL, %s)
            """
            cursor.execute(insert_student_query, (full_name, class_number, tg_name))
        student_id = cursor.lastrowid
        
        # 2. Добавляем запись в auth_users
        insert_auth_query = """
        INSERT INTO auth_users (username, password, ref_id, role) 
        VALUES (%s, %s, %s, %s)
        """
        cursor.execute(insert_auth_query, (login, generate_password_hash(password), student_id, 'student'))
        upsert_student_credentials(cursor, student_id, login, password)
        
        # Подтверждаем транзакцию
        connection.commit()
        
        return {
            "status": True,
            "message": "Студент успешно добавлен",
            "student_data": {
                "student_id": student_id,
                "full_name": full_name,
                "class": class_number,
                "login": login,
                "password": password,
                "group_id": None,
                "school_id": school_id,
                "tg_name": tg_name
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
