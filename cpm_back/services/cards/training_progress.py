"""
Прогресс заучивания карточек и настройки батчей.
"""
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

CARD_STATUS_UNLEARNED = "unlearned"
CARD_STATUS_LEARNED = "learned"
CARD_STATUS_ANSWER_CHANGED = "answer_changed"

STUDY_MODES = ("all", "unlearned", "learned", "stale")
BATCH_SIZE_PRESETS = (10, 20, 30)


def resolve_card_status(progress_row, current_fingerprint):
    if not progress_row:
        return CARD_STATUS_UNLEARNED
    if progress_row.get("content_fingerprint") == current_fingerprint:
        return CARD_STATUS_LEARNED
    return CARD_STATUS_ANSWER_CHANGED


def _empty_stats():
    return {
        "total": 0,
        "learned": 0,
        "answer_changed": 0,
        "unlearned": 0,
    }


def aggregate_card_stats(cards_with_status):
    stats = _empty_stats()
    for card in cards_with_status:
        stats["total"] += 1
        status = card.get("status", CARD_STATUS_UNLEARNED)
        if status == CARD_STATUS_LEARNED:
            stats["learned"] += 1
        elif status == CARD_STATUS_ANSWER_CHANGED:
            stats["answer_changed"] += 1
        else:
            stats["unlearned"] += 1
    return stats


def progress_percent(stats):
    total = stats.get("total") or 0
    if not total:
        return 0
    learned = stats.get("learned") or 0
    return round(learned * 100 / total)


def fetch_progress_map(cursor, student_id, card_refs):
    if not card_refs:
        return {}
    placeholders = ",".join(["%s"] * len(card_refs))
    cursor.execute(
        f"""
        SELECT card_ref, content_fingerprint, learned_at
        FROM student_card_progress
        WHERE student_id = %s AND card_ref IN ({placeholders})
        """,
        (student_id, *card_refs),
    )
    return {row["card_ref"]: row for row in cursor.fetchall()}


def attach_status_to_cards(cards, progress_map):
    result = []
    for card in cards:
        progress = progress_map.get(card["card_ref"])
        status = resolve_card_status(progress, card["content_fingerprint"])
        result.append({**card, "status": status})
    return result


def filter_cards_by_study_mode(cards, study_mode):
    if study_mode == "all":
        return cards
    if study_mode == "learned":
        return [c for c in cards if c.get("status") == CARD_STATUS_LEARNED]
    if study_mode == "stale":
        return [c for c in cards if c.get("status") == CARD_STATUS_ANSWER_CHANGED]
    return [
        c
        for c in cards
        if c.get("status")
        in (CARD_STATUS_UNLEARNED, CARD_STATUS_ANSWER_CHANGED)
    ]


def build_batches(cards, batch_size):
    size = max(1, int(batch_size or 10))
    batches = []
    for index in range(0, len(cards), size):
        chunk = cards[index : index + size]
        stats = aggregate_card_stats(chunk)
        batches.append(
            {
                "index": len(batches),
                "from": index + 1,
                "to": index + len(chunk),
                "size": len(chunk),
                "stats": stats,
            }
        )
    return batches


def get_study_settings(cursor, student_id, section_kind, section_ref_id):
    cursor.execute(
        """
        SELECT batch_size, last_batch_index, study_mode
        FROM student_section_study_settings
        WHERE student_id = %s AND section_kind = %s AND section_ref_id = %s
        """,
        (student_id, section_kind, str(section_ref_id)),
    )
    row = cursor.fetchone()
    if not row:
        return {
            "batch_size": 10,
            "last_batch_index": None,
            "study_mode": "unlearned",
        }
    return {
        "batch_size": int(row["batch_size"] or 10),
        "last_batch_index": row["last_batch_index"],
        "study_mode": row.get("study_mode") or "unlearned",
    }


def put_study_settings(student_id, section_kind, section_ref_id, payload):
    batch_size = int(payload.get("batch_size") or 10)
    if batch_size not in BATCH_SIZE_PRESETS:
        return {"success": False, "error": "batch_size должен быть 10, 20 или 30"}

    study_mode = payload.get("study_mode") or "unlearned"
    if study_mode not in STUDY_MODES:
        return {"success": False, "error": "Недопустимый study_mode"}

    last_batch_index = payload.get("last_batch_index")
    if last_batch_index is not None:
        last_batch_index = int(last_batch_index)

    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            INSERT INTO student_section_study_settings
                (student_id, section_kind, section_ref_id, batch_size, last_batch_index, study_mode)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                batch_size = VALUES(batch_size),
                last_batch_index = VALUES(last_batch_index),
                study_mode = VALUES(study_mode)
            """,
            (
                student_id,
                section_kind,
                str(section_ref_id),
                batch_size,
                last_batch_index,
                study_mode,
            ),
        )
        connection.commit()
        return {
            "success": True,
            "settings": get_study_settings(cursor, student_id, section_kind, section_ref_id),
        }
    except Exception as exc:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        if connection:
            close_db_connection(connection)


def mark_card_learned(student_id, section_kind, section_ref_id, card_ref, fingerprint):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            INSERT INTO student_card_progress
                (student_id, section_kind, section_ref_id, card_ref, content_fingerprint)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                content_fingerprint = VALUES(content_fingerprint),
                learned_at = CURRENT_TIMESTAMP
            """,
            (student_id, section_kind, str(section_ref_id), card_ref, fingerprint),
        )
        connection.commit()
        return {"success": True, "card_ref": card_ref, "status": CARD_STATUS_LEARNED}
    except Exception as exc:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        if connection:
            close_db_connection(connection)


def unmark_card_learned(student_id, card_ref):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM student_card_progress WHERE student_id = %s AND card_ref = %s",
            (student_id, card_ref),
        )
        connection.commit()
        if cursor.rowcount == 0:
            return {"success": False, "error": "Record not found"}
        return {"success": True, "card_ref": card_ref}
    except Exception as exc:
        if connection:
            connection.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        if connection:
            close_db_connection(connection)


def delete_progress_for_card_refs(cursor, card_refs):
    if not card_refs:
        return
    placeholders = ",".join(["%s"] * len(card_refs))
    cursor.execute(
        f"DELETE FROM student_card_progress WHERE card_ref IN ({placeholders})",
        tuple(card_refs),
    )


def delete_progress_for_section(cursor, section_kind, section_ref_id):
    cursor.execute(
        """
        DELETE FROM student_card_progress
        WHERE section_kind = %s AND section_ref_id = %s
        """,
        (section_kind, str(section_ref_id)),
    )
    cursor.execute(
        """
        DELETE FROM student_section_study_settings
        WHERE section_kind = %s AND section_ref_id = %s
        """,
        (section_kind, str(section_ref_id)),
    )
