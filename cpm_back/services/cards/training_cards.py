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
            topics = topics_by_section.get(section["id"], [])
            total_cards = sum(t["total_cards"] for t in topics)
            learned_cards = sum(t["learned_cards"] for t in topics)
            tree.append({
                **section,
                "topics": topics,
                "total_cards": total_cards,
                "learned_cards": learned_cards,
                "progress_percent": _progress_percent(total_cards, learned_cards),
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


def _delete_cards_for_theme(cursor, theme_id):
    cursor.execute("SELECT id FROM cards WHERE theme_id = %s", (theme_id,))
    card_ids = [row["id"] for row in cursor.fetchall()]
    if card_ids:
        placeholders = ",".join(["%s"] * len(card_ids))
        cursor.execute(
            f"DELETE FROM student_progress WHERE question_id IN ({placeholders})",
            tuple(card_ids),
        )
    cursor.execute("DELETE FROM cards WHERE theme_id = %s", (theme_id,))


def get_admin_training_catalog():
    """Разделы → тренировки → количество карточек (для админки)."""
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

        cursor.execute(
            """
            SELECT
                ct.id,
                ct.name,
                ct.section_id,
                COUNT(c.id) AS cards_count
            FROM card_themes ct
            LEFT JOIN cards c ON c.theme_id = ct.id
            GROUP BY ct.id, ct.name, ct.section_id
            ORDER BY ct.name
            """
        )
        topic_rows = cursor.fetchall()

        topics_by_section = {}
        for row in topic_rows:
            sid = row["section_id"]
            topics_by_section.setdefault(sid, []).append({
                "id": row["id"],
                "name": row["name"],
                "section_id": row["section_id"],
                "cards_count": int(row["cards_count"] or 0),
            })

        catalog = []
        for section in sections:
            topics = topics_by_section.get(section["id"], [])
            catalog.append({
                **section,
                "topics": topics,
                "topics_count": len(topics),
                "cards_count": sum(t["cards_count"] for t in topics),
            })

        return {"success": True, "sections": catalog}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if connection:
            close_db_connection(connection)


def update_training_section(section_id, name=None, sort_order=None):
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

        updates = []
        params = []
        if name is not None:
            if not str(name).strip():
                return {"success": False, "error": "name не может быть пустым"}
            updates.append("name = %s")
            params.append(str(name).strip())
        if sort_order is not None:
            updates.append("sort_order = %s")
            params.append(int(sort_order))

        if not updates:
            return {"success": False, "error": "Нет полей для обновления"}

        params.append(section_id)
        cursor.execute(
            f"UPDATE training_sections SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )
        connection.commit()
        return {"success": True, "section_id": section_id}
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


def delete_training_section(section_id):
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

        cursor.execute(
            "SELECT id FROM card_themes WHERE section_id = %s",
            (section_id,),
        )
        theme_ids = [row["id"] for row in cursor.fetchall()]
        for theme_id in theme_ids:
            _delete_cards_for_theme(cursor, theme_id)
        if theme_ids:
            placeholders = ",".join(["%s"] * len(theme_ids))
            cursor.execute(
                f"DELETE FROM card_themes WHERE id IN ({placeholders})",
                tuple(theme_ids),
            )
        cursor.execute("DELETE FROM training_sections WHERE id = %s", (section_id,))
        connection.commit()
        return {
            "success": True,
            "section_id": section_id,
            "deleted_themes": len(theme_ids),
        }
    except Exception as e:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(e)}
    finally:
        if connection:
            close_db_connection(connection)


def create_training_theme(name, section_id):
    connection = None
    try:
        if not name or not str(name).strip():
            return {"success": False, "error": "name обязателен"}
        if not section_id:
            return {"success": False, "error": "section_id обязателен"}

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id FROM training_sections WHERE id = %s",
            (section_id,),
        )
        if not cursor.fetchone():
            return {"success": False, "error": "Section not found", "section_id": section_id}

        theme_name = str(name).strip()
        cursor.execute(
            """
            SELECT id FROM card_themes
            WHERE name = %s AND section_id = %s
            """,
            (theme_name, section_id),
        )
        if cursor.fetchone():
            return {
                "success": False,
                "error": "Тренировка с таким именем уже есть в этом разделе",
            }

        cursor.execute(
            "INSERT INTO card_themes (name, section_id) VALUES (%s, %s)",
            (theme_name, section_id),
        )
        connection.commit()
        return {
            "success": True,
            "theme_id": cursor.lastrowid,
            "name": theme_name,
            "section_id": section_id,
        }
    except Exception as e:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(e)}
    finally:
        if connection:
            close_db_connection(connection)


def update_training_theme(theme_id, name=None, section_id=None):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id, section_id FROM card_themes WHERE id = %s", (theme_id,))
        theme = cursor.fetchone()
        if not theme:
            return {"success": False, "error": "Theme not found", "theme_id": theme_id}

        updates = []
        params = []
        if name is not None:
            if not str(name).strip():
                return {"success": False, "error": "name не может быть пустым"}
            updates.append("name = %s")
            params.append(str(name).strip())
        if section_id is not None:
            cursor.execute(
                "SELECT id FROM training_sections WHERE id = %s",
                (section_id,),
            )
            if not cursor.fetchone():
                return {"success": False, "error": "Section not found", "section_id": section_id}
            updates.append("section_id = %s")
            params.append(int(section_id))

        if not updates:
            return {"success": False, "error": "Нет полей для обновления"}

        target_section = section_id if section_id is not None else theme["section_id"]
        target_name = str(name).strip() if name is not None else None
        if target_name:
            cursor.execute(
                """
                SELECT id FROM card_themes
                WHERE name = %s AND section_id = %s AND id != %s
                """,
                (target_name, target_section, theme_id),
            )
            if cursor.fetchone():
                return {
                    "success": False,
                    "error": "Тренировка с таким именем уже есть в этом разделе",
                }

        params.append(theme_id)
        cursor.execute(
            f"UPDATE card_themes SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )
        connection.commit()
        return {"success": True, "theme_id": theme_id}
    except Exception as e:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(e)}
    finally:
        if connection:
            close_db_connection(connection)


def delete_training_theme(theme_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id FROM card_themes WHERE id = %s", (theme_id,))
        if not cursor.fetchone():
            return {"success": False, "error": "Theme not found", "theme_id": theme_id}

        _delete_cards_for_theme(cursor, theme_id)
        cursor.execute("DELETE FROM card_themes WHERE id = %s", (theme_id,))
        connection.commit()
        return {"success": True, "theme_id": theme_id}
    except Exception as e:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(e)}
    finally:
        if connection:
            close_db_connection(connection)


def get_cards_by_theme_admin(theme_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id FROM card_themes WHERE id = %s", (theme_id,))
        if not cursor.fetchone():
            return {"success": False, "error": "Theme not found", "theme_id": theme_id}

        cursor.execute(
            """
            SELECT id, question, answer, theme_id
            FROM cards
            WHERE theme_id = %s
            ORDER BY id
            """,
            (theme_id,),
        )
        cards = cursor.fetchall()
        return {
            "success": True,
            "theme_id": theme_id,
            "cards": cards,
            "count": len(cards),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if connection:
            close_db_connection(connection)


def create_card(theme_id, question, answer):
    connection = None
    try:
        if not question or not str(question).strip():
            return {"success": False, "error": "question обязателен"}
        if not answer or not str(answer).strip():
            return {"success": False, "error": "answer обязателен"}

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id FROM card_themes WHERE id = %s", (theme_id,))
        if not cursor.fetchone():
            return {"success": False, "error": "Theme not found", "theme_id": theme_id}

        cursor.execute(
            "INSERT INTO cards (question, answer, theme_id) VALUES (%s, %s, %s)",
            (str(question).strip(), str(answer).strip(), theme_id),
        )
        connection.commit()
        card_id = cursor.lastrowid
        return {
            "success": True,
            "card_id": card_id,
            "theme_id": theme_id,
            "question": str(question).strip(),
            "answer": str(answer).strip(),
        }
    except Exception as e:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(e)}
    finally:
        if connection:
            close_db_connection(connection)


def update_card(card_id, question=None, answer=None):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id FROM cards WHERE id = %s", (card_id,))
        if not cursor.fetchone():
            return {"success": False, "error": "Card not found", "card_id": card_id}

        updates = []
        params = []
        if question is not None:
            if not str(question).strip():
                return {"success": False, "error": "question не может быть пустым"}
            updates.append("question = %s")
            params.append(str(question).strip())
        if answer is not None:
            if not str(answer).strip():
                return {"success": False, "error": "answer не может быть пустым"}
            updates.append("answer = %s")
            params.append(str(answer).strip())

        if not updates:
            return {"success": False, "error": "Нет полей для обновления"}

        params.append(card_id)
        cursor.execute(
            f"UPDATE cards SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )
        connection.commit()
        return {"success": True, "card_id": card_id}
    except Exception as e:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(e)}
    finally:
        if connection:
            close_db_connection(connection)


def delete_card(card_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id FROM cards WHERE id = %s", (card_id,))
        if not cursor.fetchone():
            return {"success": False, "error": "Card not found", "card_id": card_id}

        cursor.execute(
            "DELETE FROM student_progress WHERE question_id = %s",
            (card_id,),
        )
        cursor.execute("DELETE FROM cards WHERE id = %s", (card_id,))
        connection.commit()
        return {"success": True, "card_id": card_id}
    except Exception as e:
        if connection:
            connection.rollback()
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
