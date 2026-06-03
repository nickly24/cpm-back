"""
Тренировочные карточки: разделы, темы, прогресс ученика.
"""
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection


def _progress_percent(total_cards, learned_cards):
    if not total_cards:
        return 0
    return round(learned_cards * 100 / total_cards)


def _topic_row(row):
    total = int(row.get("total_cards") or 0)
    learned = int(row.get("learned_cards") or 0)
    return {
        "id": row["theme_id"],
        "name": row["theme_name"],
        "section_id": row["section_id"],
        "total_cards": total,
        "learned_cards": learned,
        "progress_percent": _progress_percent(total, learned),
    }


def get_training_sections():
    """Все разделы, упорядоченные по sort_order, name."""
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, name, sort_order
            FROM training_sections
            ORDER BY sort_order, name
            """
        )
        return {"success": True, "sections": cursor.fetchall()}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if connection:
            close_db_connection(connection)


def _fetch_topics_with_progress(cursor, student_id, section_id=None):
    """Агрегированный прогресс по темам (один запрос, без N+1)."""
    params = [student_id]
    section_filter = ""
    if section_id is not None:
        section_filter = "AND ct.section_id = %s"
        params.append(section_id)

    cursor.execute(
        f"""
        SELECT
            ct.section_id,
            ct.id AS theme_id,
            ct.name AS theme_name,
            COUNT(DISTINCT c.id) AS total_cards,
            COUNT(DISTINCT sp.question_id) AS learned_cards
        FROM card_themes ct
        LEFT JOIN cards c ON c.theme_id = ct.id
        LEFT JOIN student_progress sp
            ON sp.question_id = c.id AND sp.student_id = %s
        WHERE 1=1 {section_filter}
        GROUP BY ct.section_id, ct.id, ct.name
        ORDER BY ct.name
        """,
        tuple(params),
    )
    return cursor.fetchall()


def get_topics_by_section(section_id, student_id):
    """Темы раздела с прогрессом ученика."""
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id FROM training_sections WHERE id = %s",
            (section_id,),
        )
        if not cursor.fetchone():
            return {"success": False, "error": "Section not found", "section_id": section_id}

        rows = _fetch_topics_with_progress(cursor, student_id, section_id)
        topics = [_topic_row(row) for row in rows]
        return {
            "success": True,
            "section_id": section_id,
            "student_id": student_id,
            "topics": topics,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if connection:
            close_db_connection(connection)


def get_training_tree(student_id):
    """Разделы с вложенными темами и прогрессом ученика (2 запроса)."""
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT id, name, sort_order
            FROM training_sections
            ORDER BY sort_order, name
            """
        )
        sections = cursor.fetchall()
        topic_rows = _fetch_topics_with_progress(cursor, student_id)

        topics_by_section = {}
        for row in topic_rows:
            sid = row["section_id"]
            topics_by_section.setdefault(sid, []).append(_topic_row(row))

        tree = []
        for section in sections:
            tree.append({
                **section,
                "topics": topics_by_section.get(section["id"], []),
            })

        return {"success": True, "student_id": student_id, "sections": tree}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if connection:
            close_db_connection(connection)


def get_themes(section_id=None):
    """
    Список тем card_themes.
    Без фильтра — плоский список с полем section_id (обратная совместимость).
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True, buffered=True)
        if section_id is not None:
            cursor.execute(
                """
                SELECT id, name, section_id
                FROM card_themes
                WHERE section_id = %s
                ORDER BY name
                """,
                (section_id,),
            )
        else:
            cursor.execute(
                """
                SELECT id, name, section_id
                FROM card_themes
                ORDER BY section_id, name
                """
            )
        return cursor.fetchall()
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if connection:
            close_db_connection(connection)


def create_training_section(name, sort_order=0):
    """Создать раздел тренировок."""
    connection = None
    try:
        if not name or not str(name).strip():
            return {"success": False, "error": "name обязателен"}

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "INSERT INTO training_sections (name, sort_order) VALUES (%s, %s)",
            (str(name).strip(), int(sort_order or 0)),
        )
        connection.commit()
        return {
            "success": True,
            "section_id": cursor.lastrowid,
            "name": str(name).strip(),
            "sort_order": int(sort_order or 0),
        }
    except Exception as e:
        if connection:
            connection.rollback()
        err_msg = str(e)
        if "Duplicate entry" in err_msg or "uq_training_sections_name" in err_msg:
            return {"success": False, "error": "Раздел с таким именем уже существует"}
        return {"success": False, "error": err_msg}
    finally:
        if connection:
            close_db_connection(connection)


def create_theme_with_questions(theme_name, section_id, questions=None):
    """Создать тему в разделе и добавить вопросы."""
    connection = None
    try:
        if not theme_name:
            return {"success": False, "error": "Theme name is required"}
        if not section_id:
            return {"success": False, "error": "section_id обязателен"}

        questions = questions or []
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT id FROM training_sections WHERE id = %s",
            (section_id,),
        )
        if not cursor.fetchone():
            return {"success": False, "error": "Section not found", "section_id": section_id}

        cursor.execute("SELECT id FROM card_themes WHERE name = %s", (theme_name,))
        existing_theme = cursor.fetchone()
        if existing_theme:
            theme_id = existing_theme["id"]
            message = "Theme already exists"
        else:
            cursor.execute(
                "INSERT INTO card_themes (name, section_id) VALUES (%s, %s)",
                (theme_name, section_id),
            )
            theme_id = cursor.lastrowid
            message = "Theme created successfully"
            connection.commit()

        added_questions = []
        for q in questions:
            question = q.get("question")
            answer = q.get("answer")
            if not question or not answer:
                continue
            cursor.execute(
                "INSERT INTO cards (question, answer, theme_id) VALUES (%s, %s, %s)",
                (question, answer, theme_id),
            )
            added_questions.append({
                "question": question,
                "answer": answer,
                "id": cursor.lastrowid,
            })
        connection.commit()
        return {
            "success": True,
            "message": message,
            "theme_id": theme_id,
            "theme_name": theme_name,
            "section_id": section_id,
            "added_questions": added_questions,
            "questions_count": len(added_questions),
        }
    except Exception as e:
        if connection:
            connection.rollback()
        return {"success": False, "error": "Internal server error", "details": str(e)}
    finally:
        if connection:
            close_db_connection(connection)
