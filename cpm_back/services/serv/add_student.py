from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
import random
import string

def add_student(full_name, class_number, tg_name=None):
    """
    Добавляет нового студента с автоматической генерацией логина и пароля
    
    Args:
        full_name (str): Полное имя студента
        class_number (int): Класс студента (9, 10 или 11)
        tg_name (str, optional): Telegram никнейм студента
    
    Returns:
        dict: Результат операции с данными студента
    """
    connection = None
    try:
        # Проверяем корректность класса
        if class_number not in [9, 10, 11]:
            return {
                "status": False,
                "error": "Класс должен быть 9, 10 или 11"
            }
        
        # Получаем подключение из пула
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Генерируем логин на основе имени и класса
        # Формат: первая буква имени + фамилия + класс + случайные цифры
        name_parts = full_name.strip().split()
        if len(name_parts) < 2:
            return {
                "status": False,
                "error": "Необходимо указать имя и фамилию"
            }
        
        first_name = name_parts[0].lower()
        last_name = name_parts[-1].lower()
        
        # Генерируем уникальный логин
        base_login = f"{first_name[0]}{last_name}{class_number}"
        login = base_login
        
        # Проверяем уникальность логина и добавляем цифры если нужно
        counter = 1
        while True:
            cursor.execute("SELECT 1 FROM auth_users WHERE username = %s", (login,))
            if not cursor.fetchone():
                break
            login = f"{base_login}{counter}"
            counter += 1
        
        # Генерируем пароль (8 символов: буквы + цифры)
        password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        
        # 1. Добавляем студента в таблицу students
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
        cursor.execute(insert_auth_query, (login, password, student_id, 'student'))
        
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
