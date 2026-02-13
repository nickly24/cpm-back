from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_all_exams():
    """
    Получает все экзамены из базы данных
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        query = "SELECT id, name, date FROM exams ORDER BY date DESC"
        cursor.execute(query)
        exams = cursor.fetchall()
        
        return {
            "status": True,
            "exams": exams
        }
        
    except Exception as e:
        return {
            "status": False,
            "error": str(e)
        }
    finally:
        if connection:
            close_db_connection(connection)


def get_exam_session(student_id, exam_id):
    """
    Получает сессию экзамена для студента
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        # Получаем информацию об экзамене
        cursor.execute("SELECT id, name, date FROM exams WHERE id = %s", (exam_id,))
        exam = cursor.fetchone()
        
        if not exam:
            return {
                "status": False,
                "error": "Экзамен не найден"
            }
        
        # Получаем сессию студента на этот экзамен
        cursor.execute("""
            SELECT id, val, points, examinator 
            FROM exam_sessions 
            WHERE exam_id = %s AND student_id = %s
        """, (exam_id, student_id))
        
        session = cursor.fetchone()
        
        if not session:
            return {
                "status": False,
                "error": "Сессия экзамена не найдена"
            }
        
        return {
            "status": True,
            "exam": exam,
            "grade": session["points"],  # points -> оценка (0-5)
            "score": session["val"],      # val -> баллы (0-6)
            "examinator": session["examinator"]
        }
        
    except Exception as e:
        return {
            "status": False,
            "error": str(e)
        }
    finally:
        if connection:
            close_db_connection(connection)


def get_exam_sessions_by_student(student_id):
    """
    Получает все сессии экзаменов для студента
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        query = """
            SELECT 
                es.id,
                es.val,
                es.points,
                es.examinator,
                e.id as exam_id,
                e.name as exam_name,
                e.date as exam_date
            FROM exam_sessions es
            INNER JOIN exams e ON es.exam_id = e.id
            WHERE es.student_id = %s
            ORDER BY e.date DESC
        """
        
        cursor.execute(query, (student_id,))
        raw_sessions = cursor.fetchall()
        
        # Преобразуем данные: val -> points (баллы 0-6), points -> grade (оценка 0-5)
        sessions = []
        for s in raw_sessions:
            sessions.append({
                "id": s["id"],
                "points": s["val"],  # val это баллы
                "grade": s["points"],  # points это оценка
                "examinator": s["examinator"],
                "exam_id": s["exam_id"],
                "exam_name": s["exam_name"],
                "exam_date": s["exam_date"]
            })
        
        return {
            "status": True,
            "sessions": sessions
        }
        
    except Exception as e:
        return {
            "status": False,
            "error": str(e)
        }
    finally:
        if connection:
            close_db_connection(connection)


def get_all_exam_sessions():
    """
    Получает все сессии экзаменов (для администраторов)
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        query = """
            SELECT 
                es.id,
                es.val,
                es.points,
                es.examinator,
                es.student_id,
                e.id as exam_id,
                e.name as exam_name,
                e.date as exam_date,
                s.full_name as student_name
            FROM exam_sessions es
            INNER JOIN exams e ON es.exam_id = e.id
            INNER JOIN students s ON es.student_id = s.id
            ORDER BY e.date DESC, s.full_name ASC
        """
        
        cursor.execute(query)
        raw_sessions = cursor.fetchall()
        
        # Преобразуем данные: val -> points (баллы 0-6), points -> grade (оценка 0-5)
        sessions = []
        for s in raw_sessions:
            sessions.append({
                "id": s["id"],
                "points": s["val"],  # val это баллы
                "grade": s["points"],  # points это оценка
                "examinator": s["examinator"],
                "student_id": s["student_id"],
                "exam_id": s["exam_id"],
                "exam_name": s["exam_name"],
                "exam_date": s["exam_date"],
                "student_name": s["student_name"]
            })
        
        return {
            "status": True,
            "sessions": sessions
        }
        
    except Exception as e:
        return {
            "status": False,
            "error": str(e)
        }
    finally:
        if connection:
            close_db_connection(connection)


def get_exam_sessions_by_exam(exam_id):
    """
    Получает все сессии для конкретного экзамена (для администраторов)
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        query = """
            SELECT 
                es.id,
                es.val,
                es.points,
                es.examinator,
                es.student_id,
                e.id as exam_id,
                e.name as exam_name,
                e.date as exam_date,
                s.full_name as student_name
            FROM exam_sessions es
            INNER JOIN exams e ON es.exam_id = e.id
            INNER JOIN students s ON es.student_id = s.id
            WHERE e.id = %s
            ORDER BY s.full_name ASC
        """
        
        cursor.execute(query, (exam_id,))
        raw_sessions = cursor.fetchall()
        
        # Преобразуем данные: val -> points (баллы 0-6), points -> grade (оценка 0-5)
        sessions = []
        for s in raw_sessions:
            sessions.append({
                "id": s["id"],
                "points": s["val"],  # val это баллы
                "grade": s["points"],  # points это оценка
                "examinator": s["examinator"],
                "student_id": s["student_id"],
                "exam_id": s["exam_id"],
                "exam_name": s["exam_name"],
                "exam_date": s["exam_date"],
                "student_name": s["student_name"]
            })
        
        return {
            "status": True,
            "sessions": sessions
        }
        
    except Exception as e:
        return {
            "status": False,
            "error": str(e)
        }
    finally:
        if connection:
            close_db_connection(connection)

