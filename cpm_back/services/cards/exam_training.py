"""Live classic-exam cards. Exam snapshots and examination results are untouched."""
import re
from datetime import timezone, timedelta

from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from .training_projection import content_fingerprint
from .training_progress import (
    attach_status_to_cards, aggregate_card_stats, progress_percent, build_batches,
    fetch_progress_map, get_study_settings, BATCH_SIZE_PRESETS, STUDY_MODES,
)


class TrainingError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def response(self):
        return {"success": False, "error": self.code, "message": self.message, "details": self.details, **self.details}


def parse_scope(ref):
    match = re.fullmatch(r"([1-9]\d*)(?::([1-9]\d*))?", str(ref))
    if not match:
        raise TrainingError("invalid_exam_ref", "Некорректный адрес подготовки")
    return int(match[1]), int(match[2]) if match[2] else None


def exam_card_ref(exam_id, question_id):
    return f"exam:{exam_id}:{question_id}"


def parse_exam_card_ref(ref):
    match = re.fullmatch(r"exam:([1-9]\d*):([1-9]\d*)", str(ref))
    if not match:
        raise TrainingError("question_not_found", "Вопрос удалён из экзамена")
    return int(match[1]), int(match[2])


def _iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        # Classic exam SQL dates are stored in UTC, including naive datetimes.
        return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.isoformat()
    return str(value)


def project_question(row):
    question, answer = row["question_text"], row["answer_text"]
    return {
        "card_ref": exam_card_ref(row["exam_id"], row["id"]),
        "question_id": row["id"], "exam_id": row["exam_id"],
        "part_id": row["part_id"], "part_code": row["part_code"],
        "question": question, "answer": answer, "sort_order": row["sort_order"],
        "content_fingerprint": content_fingerprint(question, answer),
    }


def load_exam(cursor, exam_id, *, preview=False, lock=False):
    cursor.execute(
        """SELECT e.*,d.name direction_name,s.start_at,s.end_at
        FROM exams e LEFT JOIN directions d ON d.id=e.direction_id
        LEFT JOIN classic_exam_settings s ON s.exam_id=e.id WHERE e.id=%s"""
        + (" FOR UPDATE" if lock else ""), (exam_id,),
    )
    exam = cursor.fetchone()
    if not exam:
        raise TrainingError("exam_not_found", "Экзамен не найден")
    if exam["exam_type"] != "classic":
        raise TrainingError("wrong_exam_type", "Подготовка доступна только для классического экзамена")
    if not preview and not exam.get("training_enabled"):
        raise TrainingError("training_disabled", "Подготовка по этому экзамену сейчас недоступна")
    return exam


def load_bank(cursor, exam_id, *, preview=False, lock=False):
    exam = load_exam(cursor, exam_id, preview=preview, lock=lock)
    cursor.execute("SELECT id,code,sort_order FROM classic_exam_parts WHERE exam_id=%s ORDER BY sort_order,id", (exam_id,))
    parts = cursor.fetchall()
    cursor.execute(
        """SELECT q.*,p.code part_code FROM classic_exam_questions q
        JOIN classic_exam_parts p ON p.id=q.part_id AND p.exam_id=q.exam_id
        WHERE q.exam_id=%s ORDER BY p.sort_order,q.sort_order,q.id""", (exam_id,),
    )
    cards = [project_question(q) for q in cursor.fetchall()
             if (q.get("question_text") or "").strip() and (q.get("answer_text") or "").strip()]
    return exam, parts, cards


def exam_dto(exam):
    return {
        "id": exam["id"], "examType": "classic", "directionId": exam["direction_id"],
        "directionName": exam.get("direction_name") or exam.get("name") or "",
        "startAt": _iso(exam.get("start_at")), "endAt": _iso(exam.get("end_at")),
        "trainingEnabled": bool(exam.get("training_enabled")), "version": exam["version"],
    }


def _stats(cards):
    values = aggregate_card_stats(cards)
    return {**values, "progress_percent": progress_percent(values)}


def _node(ref, name, cards, **extra):
    stats = _stats(cards)
    return {
        "kind": "exam", "refId": str(ref), "name": name, "stats": stats,
        "total_cards": stats["total"], "learned_cards": stats["learned"],
        "answer_changed_cards": stats["answer_changed"], "progress_percent": stats["progress_percent"], **extra,
    }


def _name(exam):
    return f"{exam.get('direction_name') or exam.get('name') or 'Экзамен'} · Экзамен №{exam['id']}"


def section_node(cursor, student_id, exam_id, *, admin=False):
    exam, parts, cards = load_bank(cursor, exam_id, preview=admin)
    if not cards and not admin:
        return None
    progress = fetch_progress_map(cursor, student_id, [c["card_ref"] for c in cards]) if student_id is not None else {}
    cards = attach_status_to_cards(cards, progress)
    periods = []
    for value in (exam.get("start_at"), exam.get("end_at")):
        if value and hasattr(value, "strftime"):
            local = value + timedelta(hours=3)
            periods.extend([local.strftime("%d.%m.%Y"), local.strftime("%Y-%m-%d"), local.strftime("%H:%M")])
    return _node(exam_id, _name(exam), cards, exam_id=exam_id, parts_count=len(parts),
                 start_at=_iso(exam.get("start_at")), end_at=_iso(exam.get("end_at")),
                 search_text=" ".join(periods) or "Период не задан",
                 training_enabled=bool(exam.get("training_enabled")))


def catalog_nodes(cursor, student_id, direction_id=None, *, admin=False):
    sql = "SELECT id,direction_id FROM exams WHERE exam_type='classic'"
    params = []
    if not admin:
        sql += " AND training_enabled=1"
    if direction_id is not None:
        sql += " AND direction_id=%s"
        params.append(direction_id)
    cursor.execute(sql + " ORDER BY id", tuple(params))
    exams = cursor.fetchall()
    nodes = []
    for exam in exams:
        node = section_node(cursor, student_id, exam["id"], admin=admin)
        if node:
            nodes.append({**node, "direction_id": exam["direction_id"]})
    return nodes


def study_view(cursor, student_id, ref, *, preview=False):
    exam_id, part_id = parse_scope(ref)
    exam, part_rows, cards = load_bank(cursor, exam_id, preview=preview)
    part = next((p for p in part_rows if p["id"] == part_id), None)
    if part_id is not None and not part:
        raise TrainingError("part_not_found", "Часть экзамена удалена")
    if not cards and not preview:
        raise TrainingError("exam_empty", "В экзамене пока нет карточек для подготовки")
    progress = {} if preview else fetch_progress_map(cursor, student_id, [c["card_ref"] for c in cards])
    cards = attach_status_to_cards(cards, progress)
    parts = [_node(f"{exam_id}:{p['id']}", f"Часть {p['code']}", [c for c in cards if c["part_id"] == p["id"]],
                   exam_id=exam_id, part_id=p["id"], part_code=p["code"], sort_order=p["sort_order"])
             for p in part_rows]
    selected = [c for c in cards if c["part_id"] == part_id] if part_id is not None else cards
    settings = ({"batch_size": 10, "last_batch_index": 0, "study_mode": "unlearned", "last_part_id": None}
                if preview else get_study_settings(cursor, student_id, "exam", ref))
    if not preview:
        root_settings = settings if part_id is None else get_study_settings(cursor, student_id, "exam", exam_id)
        last_part = root_settings.get("last_part_id")
        # None is an intentionally selected whole exam; 0 means a deleted part.
        settings["last_part_id"] = last_part if last_part is None or any(p["id"] == last_part for p in part_rows) else 0
    batches = build_batches(selected, settings["batch_size"])
    if settings.get("last_batch_index") is None or settings["last_batch_index"] < -1 or settings["last_batch_index"] >= max(1, len(batches)):
        settings["last_batch_index"] = 0
    return {
        "success": True, "student_id": student_id, "section_kind": "exam", "section_ref_id": str(ref),
        "section_name": f"{_name(exam)} · Часть {part['code']}" if part else _name(exam),
        "exam": exam_dto(exam), "parts": parts, "part_id": part_id,
        "cards": selected, "stats": _stats(selected), "exam_stats": _stats(cards),
        "batches": batches, "settings": settings, "preview": preview,
    }


def _run_write(action):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        result = action(cursor)
        connection.commit()
        return result
    except TrainingError as exc:
        if connection:
            connection.rollback()
        return exc.response()
    except Exception:
        if connection:
            connection.rollback()
        return {"success": False, "error": "training_save_failed", "message": "Не удалось сохранить прогресс. Повторите попытку"}
    finally:
        if connection:
            close_db_connection(connection)


def save_progress(student_id, ref, card_ref, fingerprint=None, *, remove=False):
    def action(cursor):
        exam_id, part_id = parse_scope(ref)
        card_exam_id, question_id = parse_exam_card_ref(card_ref)
        if card_exam_id != exam_id:
            raise TrainingError("question_not_found", "Вопрос удалён из экзамена")
        load_exam(cursor, exam_id, lock=True)
        cursor.execute(
            """SELECT q.*,p.code part_code FROM classic_exam_questions q
            JOIN classic_exam_parts p ON p.id=q.part_id AND p.exam_id=q.exam_id
            WHERE q.exam_id=%s AND q.id=%s""", (exam_id, question_id),
        )
        row = cursor.fetchone()
        if (not row or (part_id is not None and row["part_id"] != part_id)
                or not (row.get("question_text") or "").strip()
                or not (row.get("answer_text") or "").strip()):
            raise TrainingError("question_not_found", "Вопрос удалён из экзамена")
        card = project_question(row)
        if not remove and fingerprint != card["content_fingerprint"]:
            current = attach_status_to_cards([card], fetch_progress_map(cursor, student_id, [card_ref]))[0]
            raise TrainingError("content_changed", "Содержание карточки изменилось. Ознакомьтесь с обновлённым вопросом и ответом", card=current)
        if remove:
            cursor.execute("DELETE FROM student_card_progress WHERE student_id=%s AND section_kind='exam' AND card_ref=%s", (student_id, card_ref))
        else:
            cursor.execute(
                """INSERT INTO student_card_progress(student_id,section_kind,section_ref_id,card_ref,content_fingerprint)
                VALUES (%s,'exam',%s,%s,%s) ON DUPLICATE KEY UPDATE
                content_fingerprint=VALUES(content_fingerprint),learned_at=CURRENT_TIMESTAMP""",
                (student_id, str(exam_id), card_ref, card["content_fingerprint"]),
            )
        return {"success": True, "card_ref": card_ref, "status": "unlearned" if remove else "learned"}
    return _run_write(action)


def save_settings(student_id, ref, payload):
    def action(cursor):
        exam_id, part_id = parse_scope(ref)
        _, parts, cards = load_bank(cursor, exam_id, lock=True)
        ids = {p["id"] for p in parts}
        if part_id is not None and part_id not in ids:
            raise TrainingError("part_not_found", "Часть экзамена удалена")
        if not cards:
            raise TrainingError("exam_empty", "В экзамене пока нет карточек для подготовки")
        settings = get_study_settings(cursor, student_id, "exam", ref)
        size = payload.get("batch_size", settings["batch_size"])
        mode = payload.get("study_mode", settings["study_mode"])
        index = payload.get("last_batch_index", settings.get("last_batch_index") or 0)
        if type(size) is not int or size not in BATCH_SIZE_PRESETS:
            raise TrainingError("invalid_batch_size", "Размер набора должен быть 10, 20 или 30")
        if mode not in STUDY_MODES:
            raise TrainingError("invalid_study_mode", "Недопустимый режим обучения")
        if index is None:
            index = 0
        if type(index) is not int or index < -1:
            raise TrainingError("invalid_batch_index", "Некорректный номер набора")
        count = len([c for c in cards if part_id is None or c["part_id"] == part_id])
        if index >= max(1, (count + size - 1) // size):
            index = 0
        root = get_study_settings(cursor, student_id, "exam", exam_id)
        last_part = payload.get("last_part_id", part_id)
        if last_part is not None and (type(last_part) is not int or last_part not in ids):
            raise TrainingError("part_not_found", "Часть экзамена удалена")
        cursor.execute(
            """INSERT INTO student_section_study_settings
            (student_id,section_kind,section_ref_id,batch_size,last_batch_index,study_mode,last_part_id)
            VALUES (%s,'exam',%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
            batch_size=VALUES(batch_size),last_batch_index=VALUES(last_batch_index),study_mode=VALUES(study_mode),last_part_id=VALUES(last_part_id)""",
            (student_id, str(ref), size, index, mode, last_part),
        )
        if part_id is not None:
            cursor.execute(
                """INSERT INTO student_section_study_settings
                (student_id,section_kind,section_ref_id,batch_size,last_batch_index,study_mode,last_part_id)
                VALUES (%s,'exam',%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE last_part_id=VALUES(last_part_id)""",
                (student_id, str(exam_id), root["batch_size"], root["last_batch_index"], root["study_mode"], last_part),
            )
        return {"success": True, "settings": {"batch_size": size, "last_batch_index": index, "study_mode": mode, "last_part_id": last_part}}
    return _run_write(action)


def delete_question_progress(cursor, exam_id, question_id):
    cursor.execute("DELETE FROM student_card_progress WHERE section_kind='exam' AND card_ref=%s", (exam_card_ref(exam_id, question_id),))


def delete_part_progress(cursor, exam_id, part_id):
    cursor.execute(
        """DELETE progress FROM student_card_progress progress
        JOIN classic_exam_questions q ON progress.card_ref=CONCAT('exam:',q.exam_id,':',q.id)
        WHERE progress.section_kind='exam' AND q.exam_id=%s AND q.part_id=%s""", (exam_id, part_id),
    )
    cursor.execute("DELETE FROM student_section_study_settings WHERE section_kind='exam' AND section_ref_id=%s", (f"{exam_id}:{part_id}",))
    cursor.execute("UPDATE student_section_study_settings SET last_part_id=0 WHERE section_kind='exam' AND section_ref_id=%s AND last_part_id=%s", (str(exam_id), part_id))


def delete_exam_progress(cursor, exam_id):
    cursor.execute("DELETE FROM student_card_progress WHERE section_kind='exam' AND section_ref_id=%s", (str(exam_id),))
    cursor.execute("DELETE FROM student_section_study_settings WHERE section_kind='exam' AND (section_ref_id=%s OR section_ref_id LIKE %s)", (str(exam_id), f"{exam_id}:%"))
