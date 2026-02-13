from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_zaps_by_student(student_id):
    """
    Получает все запросы на отгул студента
    
    Args:
        student_id: ID студента
    
    Returns:
        dict: Список запросов
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT 
                id,
                student_id,
                text,
                status,
                answer
            FROM zaps
            WHERE student_id = %s
            ORDER BY id DESC
        """, (student_id,))

        zaps = cursor.fetchall()

        return {
            "status": True,
            "zaps": zaps
        }

    except Exception as err:
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)

def get_all_zaps(status=None):
    """
    Получает все запросы на отгул (для админов)
    
    Args:
        status: Фильтр по статусу ('set', 'apr', 'dec')
    
    Returns:
        dict: Список запросов с информацией о студентах
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT 
                z.id,
                z.student_id,
                z.text,
                z.status,
                z.answer,
                s.full_name
            FROM zaps z
            JOIN students s ON z.student_id = s.id
        """
        
        params = []
        if status:
            query += " WHERE z.status = %s"
            params.append(status)
        
        query += " ORDER BY z.id DESC"

        cursor.execute(query, tuple(params))
        zaps = cursor.fetchall()

        return {
            "status": True,
            "zaps": zaps
        }

    except Exception as err:
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)

def get_zap_by_id(zap_id):
    """
    Получает запрос на отгул по ID
    
    Args:
        zap_id: ID запроса
    
    Returns:
        dict: Информация о запросе с изображениями
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        # Получаем информацию о запросе
        cursor.execute("""
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
        """, (zap_id,))

        zap = cursor.fetchone()
        
        if not zap:
            return {"status": False, "error": "Запрос не найден"}

        # Получаем изображения с типом
        cursor.execute("SELECT id, img, type FROM zap_img WHERE zap_id = %s", (zap_id,))
        images = cursor.fetchall()

        return {
            "status": True,
            "zap": zap,
            "images": images
        }

    except Exception as err:
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)

