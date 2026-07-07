"""
Тренировочные карточки v2: направления, manual/test разделы, батчи, статусы.
"""
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from cpm_back.services.exam.get_directions import get_directions
from cpm_back.services.cards.training_projection import (
    get_test_training_cards,
    get_visible_training_tests,
    manual_card_ref,
    project_manual_card,
    test_allows_training,
)
from cpm_back.services.cards.training_progress import (
    aggregate_card_stats,
    attach_status_to_cards,
    build_batches,
    delete_progress_for_card_refs,
    delete_progress_for_section,
    fetch_progress_map,
    filter_cards_by_study_mode,
    get_study_settings,
    mark_card_learned,
    progress_percent,
    put_study_settings,
    unmark_card_learned,
)


def _section_stats(cards_with_status):
    stats = aggregate_card_stats(cards_with_status)
    return {
        **stats,
        "progress_percent": progress_percent(stats),
    }


def _load_manual_cards_for_theme(cursor, theme_id):
    cursor.execute(
        """
        SELECT id, question, answer, theme_id, sort_order
        FROM cards
        WHERE theme_id = %s
        ORDER BY sort_order, id
        """,
        (theme_id,),
    )
    return [project_manual_card(row) for row in cursor.fetchall()]


def _load_manual_section_cards(cursor, student_id, theme_id):
    cards = _load_manual_cards_for_theme(cursor, theme_id)
    progress_map = fetch_progress_map(
        cursor, student_id, [card["card_ref"] for card in cards]
    )
    return attach_status_to_cards(cards, progress_map)


def _load_test_section_cards(student_id, test_id):
    cards, err = get_test_training_cards(test_id)
    if err:
        return None, err

    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        progress_map = fetch_progress_map(
            cursor, student_id, [card["card_ref"] for card in cards]
        )
        return attach_status_to_cards(cards, progress_map), None
    finally:
        if connection:
            close_db_connection(connection)


def _manual_section_node(cursor, student_id, theme_row):
    theme_id = theme_row["id"]
    cards = _load_manual_section_cards(cursor, student_id, theme_id)
    stats = _section_stats(cards)
    return {
        "kind": "manual",
        "refId": str(theme_id),
        "name": theme_row["name"],
        "stats": stats,
        "total_cards": stats["total"],
        "learned_cards": stats["learned"],
        "answer_changed_cards": stats["answer_changed"],
        "progress_percent": stats["progress_percent"],
    }


def _test_section_node(student_id, test_item):
    test_id = test_item["id"]
    cards, err = _load_test_section_cards(student_id, test_id)
    if err:
        return None

    stats = _section_stats(cards)
    title = test_item.get("title") or "Тест"
    return {
        "kind": "test",
        "refId": test_id,
        "name": title,
        "sourceTestTitle": title,
        "stats": stats,
        "total_cards": stats["total"],
        "learned_cards": stats["learned"],
        "answer_changed_cards": stats["answer_changed"],
        "progress_percent": stats["progress_percent"],
    }


def get_training_tree(student_id):
    """Направления с manual- и test-разделами."""
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        directions = get_directions() or []
        direction_names = {d["name"]: d for d in directions}
        direction_ids = {d["id"]: d for d in directions}

        cursor.execute(
            """
            SELECT id, name, direction_id
            FROM card_themes
            ORDER BY name
            """
        )
        theme_rows = cursor.fetchall()
        themes_by_direction = {}
        for row in theme_rows:
            themes_by_direction.setdefault(row["direction_id"], []).append(row)

        visible_tests = get_visible_training_tests()
        tests_by_direction = {}
        for test in visible_tests:
            direction_name = test.get("direction")
            if direction_name:
                tests_by_direction.setdefault(direction_name, []).append(test)

        tree = []
        seen_direction_ids = set()

        for direction in directions:
            did = direction["id"]
            seen_direction_ids.add(did)
            sections = []
            for theme_row in themes_by_direction.get(did, []):
                sections.append(_manual_section_node(cursor, student_id, theme_row))
            for test_item in tests_by_direction.get(direction["name"], []):
                node = _test_section_node(student_id, test_item)
                if node:
                    sections.append(node)

            if not sections:
                continue

            total_cards = sum(s["total_cards"] for s in sections)
            learned_cards = sum(s["learned_cards"] for s in sections)
            answer_changed_cards = sum(
                s.get("answer_changed_cards", 0) for s in sections
            )
            stats = {
                "total": total_cards,
                "learned": learned_cards,
                "answer_changed": answer_changed_cards,
                "unlearned": max(
                    0, total_cards - learned_cards - answer_changed_cards
                ),
            }
            tree.append(
                {
                    "id": did,
                    "name": direction["name"],
                    "sections": sections,
                    "topics": sections,
                    "total_cards": total_cards,
                    "learned_cards": learned_cards,
                    "answer_changed_cards": answer_changed_cards,
                    "progress_percent": progress_percent(stats),
                }
            )

        for direction_name, tests in tests_by_direction.items():
            direction = direction_names.get(direction_name)
            if direction and direction["id"] in seen_direction_ids:
                continue
            sections = []
            for test_item in tests:
                node = _test_section_node(student_id, test_item)
                if node:
                    sections.append(node)
            if not sections:
                continue
            total_cards = sum(s["total_cards"] for s in sections)
            learned_cards = sum(s["learned_cards"] for s in sections)
            answer_changed_cards = sum(
                s.get("answer_changed_cards", 0) for s in sections
            )
            stats = {
                "total": total_cards,
                "learned": learned_cards,
                "answer_changed": answer_changed_cards,
                "unlearned": max(
                    0, total_cards - learned_cards - answer_changed_cards
                ),
            }
            tree.append(
                {
                    "id": direction["id"] if direction else 0,
                    "name": direction_name,
                    "sections": sections,
                    "topics": sections,
                    "total_cards": total_cards,
                    "learned_cards": learned_cards,
                    "answer_changed_cards": answer_changed_cards,
                    "progress_percent": progress_percent(stats),
                }
            )

        return {"success": True, "student_id": student_id, "directions": tree}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        if connection:
            close_db_connection(connection)


def get_section_study_view(student_id, section_kind, section_ref_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if section_kind == "manual":
            cursor.execute(
                "SELECT id, name, direction_id FROM card_themes WHERE id = %s",
                (section_ref_id,),
            )
            theme = cursor.fetchone()
            if not theme:
                return {"success": False, "error": "Section not found"}
            cards = _load_manual_section_cards(cursor, student_id, int(section_ref_id))
            section_name = theme["name"]
        elif section_kind == "test":
            cards, err = _load_test_section_cards(student_id, str(section_ref_id))
            if err == "answers_hidden":
                return {"success": False, "error": "answers_hidden"}
            if err:
                return {"success": False, "error": err}
            from cpm_back.services.exam.test_definition_cache import (
                get_test_document_cached,
            )

            test = get_test_document_cached(str(section_ref_id))
            section_name = (test or {}).get("title") or "Тест"
        else:
            return {"success": False, "error": "Invalid section kind"}

        settings = get_study_settings(
            cursor, student_id, section_kind, section_ref_id
        )
        batches = build_batches(cards, settings["batch_size"])
        stats = _section_stats(cards)

        return {
            "success": True,
            "student_id": student_id,
            "section_kind": section_kind,
            "section_ref_id": str(section_ref_id),
            "section_name": section_name,
            "cards": cards,
            "stats": {**stats, "progress_percent": stats.get("progress_percent", progress_percent(stats))},
            "batches": batches,
            "settings": settings,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        if connection:
            close_db_connection(connection)


def get_section_batch_cards(
    student_id, section_kind, section_ref_id, batch_index, study_mode=None
):
    view = get_section_study_view(student_id, section_kind, section_ref_id)
    if not view.get("success"):
        return view

    settings = view["settings"]
    mode = study_mode or settings.get("study_mode") or "unlearned"
    all_cards = view["cards"]
    batches = view["batches"]

    if batch_index < 0 or batch_index >= len(batches):
        return {"success": False, "error": "Batch not found"}

    batch_meta = batches[batch_index]
    start = batch_meta["from"] - 1
    end = batch_meta["to"]
    batch_cards = all_cards[start:end]
    filtered = filter_cards_by_study_mode(batch_cards, mode)

    return {
        "success": True,
        "student_id": student_id,
        "section_kind": section_kind,
        "section_ref_id": str(section_ref_id),
        "batch_index": batch_index,
        "batch": batch_meta,
        "study_mode": mode,
        "cards": filtered,
        "count": len(filtered),
    }


def update_section_study_settings(student_id, section_kind, section_ref_id, payload):
    return put_study_settings(student_id, section_kind, section_ref_id, payload)


def mark_section_card_learned(
    student_id, section_kind, section_ref_id, card_ref, fingerprint
):
    if section_kind == "test":
        from cpm_back.services.exam.test_definition_cache import get_test_document_cached

        test = get_test_document_cached(str(section_ref_id))
        if not test_allows_training(test):
            return {"success": False, "error": "answers_hidden"}

    return mark_card_learned(
        student_id, section_kind, section_ref_id, card_ref, fingerprint
    )


def get_admin_training_catalog():
    """Направления → manual-разделы → карточки."""
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        directions = get_directions() or []

        cursor.execute(
            """
            SELECT ct.id, ct.name, ct.direction_id, COUNT(c.id) AS cards_count
            FROM card_themes ct
            LEFT JOIN cards c ON c.theme_id = ct.id
            GROUP BY ct.id, ct.name, ct.direction_id
            ORDER BY ct.name
            """
        )
        theme_rows = cursor.fetchall()
        themes_by_direction = {}
        for row in theme_rows:
            themes_by_direction.setdefault(row["direction_id"], []).append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "direction_id": row["direction_id"],
                    "cards_count": int(row["cards_count"] or 0),
                }
            )

        catalog = []
        for direction in directions:
            sections = themes_by_direction.get(direction["id"], [])
            catalog.append(
                {
                    "id": direction["id"],
                    "name": direction["name"],
                    "sections": sections,
                    "topics": sections,
                    "topics_count": len(sections),
                    "cards_count": sum(s["cards_count"] for s in sections),
                }
            )

        return {"success": True, "directions": catalog}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        if connection:
            close_db_connection(connection)


def _delete_cards_for_theme(cursor, theme_id):
    cursor.execute("SELECT id FROM cards WHERE theme_id = %s", (theme_id,))
    card_ids = [row["id"] for row in cursor.fetchall()]
    if card_ids:
        refs = [manual_card_ref(card_id) for card_id in card_ids]
        delete_progress_for_card_refs(cursor, refs)
    cursor.execute("DELETE FROM cards WHERE theme_id = %s", (theme_id,))


def create_training_theme(name, direction_id):
    connection = None
    try:
        if not name or not str(name).strip():
            return {"success": False, "error": "name обязателен"}
        if not direction_id:
            return {"success": False, "error": "direction_id обязателен"}

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id FROM directions WHERE id = %s", (direction_id,))
        if not cursor.fetchone():
            return {
                "success": False,
                "error": "Direction not found",
                "direction_id": direction_id,
            }

        theme_name = str(name).strip()
        cursor.execute(
            """
            SELECT id FROM card_themes
            WHERE name = %s AND direction_id = %s
            """,
            (theme_name, direction_id),
        )
        if cursor.fetchone():
            return {
                "success": False,
                "error": "Раздел с таким именем уже есть в этом направлении",
            }

        cursor.execute(
            "INSERT INTO card_themes (name, direction_id) VALUES (%s, %s)",
            (theme_name, direction_id),
        )
        connection.commit()
        return {
            "success": True,
            "theme_id": cursor.lastrowid,
            "name": theme_name,
            "direction_id": direction_id,
        }
    except Exception as exc:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        if connection:
            close_db_connection(connection)


def update_training_theme(theme_id, name=None, direction_id=None):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, direction_id FROM card_themes WHERE id = %s", (theme_id,)
        )
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
        if direction_id is not None:
            cursor.execute("SELECT id FROM directions WHERE id = %s", (direction_id,))
            if not cursor.fetchone():
                return {
                    "success": False,
                    "error": "Direction not found",
                    "direction_id": direction_id,
                }
            updates.append("direction_id = %s")
            params.append(int(direction_id))

        if not updates:
            return {"success": False, "error": "Нет полей для обновления"}

        target_direction = (
            direction_id if direction_id is not None else theme["direction_id"]
        )
        target_name = str(name).strip() if name is not None else None
        if target_name:
            cursor.execute(
                """
                SELECT id FROM card_themes
                WHERE name = %s AND direction_id = %s AND id != %s
                """,
                (target_name, target_direction, theme_id),
            )
            if cursor.fetchone():
                return {
                    "success": False,
                    "error": "Раздел с таким именем уже есть в этом направлении",
                }

        params.append(theme_id)
        cursor.execute(
            f"UPDATE card_themes SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )
        connection.commit()
        return {"success": True, "theme_id": theme_id}
    except Exception as exc:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(exc)}
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
        delete_progress_for_section(cursor, "manual", theme_id)
        cursor.execute("DELETE FROM card_themes WHERE id = %s", (theme_id,))
        connection.commit()
        return {"success": True, "theme_id": theme_id}
    except Exception as exc:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(exc)}
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
            SELECT id, question, answer, theme_id, sort_order
            FROM cards
            WHERE theme_id = %s
            ORDER BY sort_order, id
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
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        if connection:
            close_db_connection(connection)


def create_card(theme_id, question, answer, sort_order=None):
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

        if sort_order is None:
            cursor.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM cards WHERE theme_id = %s",
                (theme_id,),
            )
            sort_order = int(cursor.fetchone()["next_order"])

        cursor.execute(
            """
            INSERT INTO cards (question, answer, theme_id, sort_order)
            VALUES (%s, %s, %s, %s)
            """,
            (str(question).strip(), str(answer).strip(), theme_id, int(sort_order)),
        )
        connection.commit()
        card_id = cursor.lastrowid
        return {
            "success": True,
            "card_id": card_id,
            "theme_id": theme_id,
            "question": str(question).strip(),
            "answer": str(answer).strip(),
            "sort_order": int(sort_order),
        }
    except Exception as exc:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        if connection:
            close_db_connection(connection)


def update_card(card_id, question=None, answer=None, sort_order=None):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, question, answer FROM cards WHERE id = %s", (card_id,)
        )
        existing = cursor.fetchone()
        if not existing:
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
        if sort_order is not None:
            updates.append("sort_order = %s")
            params.append(int(sort_order))

        if not updates:
            return {"success": False, "error": "Нет полей для обновления"}

        params.append(card_id)
        cursor.execute(
            f"UPDATE cards SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )
        connection.commit()
        return {"success": True, "card_id": card_id}
    except Exception as exc:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(exc)}
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

        delete_progress_for_card_refs(cursor, [manual_card_ref(card_id)])
        cursor.execute("DELETE FROM cards WHERE id = %s", (card_id,))
        connection.commit()
        return {"success": True, "card_id": card_id}
    except Exception as exc:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        if connection:
            close_db_connection(connection)


def create_theme_with_questions(theme_name, direction_id, questions=None):
    connection = None
    try:
        if not theme_name:
            return {"success": False, "error": "Theme name is required"}
        if not direction_id:
            return {"success": False, "error": "direction_id обязателен"}

        questions = questions or []
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT id FROM directions WHERE id = %s", (direction_id,))
        if not cursor.fetchone():
            return {
                "success": False,
                "error": "Direction not found",
                "direction_id": direction_id,
            }

        cursor.execute(
            "SELECT id FROM card_themes WHERE name = %s AND direction_id = %s",
            (theme_name, direction_id),
        )
        existing_theme = cursor.fetchone()
        if existing_theme:
            theme_id = existing_theme["id"]
            message = "Theme already exists"
        else:
            cursor.execute(
                "INSERT INTO card_themes (name, direction_id) VALUES (%s, %s)",
                (theme_name, direction_id),
            )
            theme_id = cursor.lastrowid
            message = "Theme created successfully"
            connection.commit()

        added_questions = []
        sort_order = 0
        for item in questions:
            question = item.get("question")
            answer = item.get("answer")
            if not question or not answer:
                continue
            sort_order += 1
            cursor.execute(
                """
                INSERT INTO cards (question, answer, theme_id, sort_order)
                VALUES (%s, %s, %s, %s)
                """,
                (question, answer, theme_id, sort_order),
            )
            added_questions.append(
                {
                    "question": question,
                    "answer": answer,
                    "id": cursor.lastrowid,
                    "sort_order": sort_order,
                }
            )
        connection.commit()
        return {
            "success": True,
            "message": message,
            "theme_id": theme_id,
            "theme_name": theme_name,
            "direction_id": direction_id,
            "added_questions": added_questions,
            "questions_count": len(added_questions),
        }
    except Exception as exc:
        if connection:
            connection.rollback()
        return {"success": False, "error": "Internal server error", "details": str(exc)}
    finally:
        if connection:
            close_db_connection(connection)
