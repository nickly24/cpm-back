"""Выполнение импорта пользователей с откатом при ошибке."""
from __future__ import annotations

import random
import string
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from werkzeug.security import generate_password_hash

from cpm_back.db.mysql_pool import close_db_connection, get_db_connection
from cpm_back.services.user_import.person_name import normalize_person_name
from cpm_back.services.user_import.school_lookup import is_schools_schema_ready

from .import_jobs import _update_job, get_import_job, save_job_results


ProgressCallback = Optional[Callable[[Dict[str, Any]], None]]


def _empty_entities() -> Dict[str, List[Any]]:
    return {
        "school_ids_created": [],
        "group_ids_created": [],
        "proctor_ids_created": [],
        "student_ids_created": [],
        "auth_usernames_created": [],
    }


def _generate_student_login(cursor, first_name: str, last_name: str, class_number: int) -> str:
    base_login = f"{first_name[0].lower()}{last_name.lower()}{class_number}"
    login = base_login
    counter = 1
    while True:
        cursor.execute("SELECT 1 FROM auth_users WHERE username = %s", (login,))
        if not cursor.fetchone():
            return login
        login = f"{base_login}{counter}"
        counter += 1


def _generate_proctor_login(cursor, first_name: str, last_name: str) -> str:
    base_login = f"pr{first_name[0].lower()}{last_name.lower()}"
    login = base_login
    counter = 1
    while True:
        cursor.execute("SELECT 1 FROM auth_users WHERE username = %s", (login,))
        if not cursor.fetchone():
            return login
        login = f"{base_login}{counter}"
        counter += 1


def _generate_password() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=8))


def _rollback_entities(cursor, entities: Dict[str, List[Any]]) -> None:
    for student_id in reversed(entities.get("student_ids_created") or []):
        cursor.execute("DELETE FROM auth_users WHERE role = 'student' AND ref_id = %s", (student_id,))
        cursor.execute("DELETE FROM students WHERE id = %s", (student_id,))

    for proctor_id in reversed(entities.get("proctor_ids_created") or []):
        cursor.execute("DELETE FROM auth_users WHERE role = 'proctor' AND ref_id = %s", (proctor_id,))
        cursor.execute("DELETE FROM proctors WHERE id = %s", (proctor_id,))

    for group_id in reversed(entities.get("group_ids_created") or []):
        cursor.execute("UPDATE students SET group_id = NULL WHERE group_id = %s", (group_id,))
        cursor.execute("UPDATE proctors SET group_id = NULL WHERE group_id = %s", (group_id,))
        cursor.execute("DELETE FROM `groups` WHERE id = %s", (group_id,))

    schools_ready = is_schools_schema_ready(cursor)
    for school_id in reversed(entities.get("school_ids_created") or []):
        if not schools_ready:
            continue
        cursor.execute("SELECT COUNT(*) AS cnt FROM students WHERE school_id = %s", (school_id,))
        row = cursor.fetchone()
        count = row["cnt"] if isinstance(row, dict) else row[0]
        if count == 0:
            cursor.execute("DELETE FROM schools WHERE id = %s", (school_id,))


def _load_preview_for_job(cursor, job: Dict[str, Any]) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT preview_payload FROM user_import_sessions WHERE id = %s
        """,
        (job["session_id"],),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("Сессия импорта не найдена")

    payload = row.get("preview_payload")
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)
    return payload


def run_users_import(job_id: int, progress_callback: ProgressCallback = None) -> None:
    conn = None
    entities = _empty_entities()
    result_rows: List[Dict[str, Any]] = []
    successful = 0
    skipped = 0
    processed = 0

    try:
        job = get_import_job(job_id)
        if not job:
            return

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        preview = _load_preview_for_job(cursor, job)
        students = preview.get("students") or []
        schools_map = {item["key"]: item for item in preview.get("schools") or []}
        groups_map = {item["key"]: item for item in preview.get("groups") or []}
        proctors_map = {item["key"]: item for item in preview.get("proctors") or []}

        school_ids: Dict[str, int] = {}
        group_ids: Dict[str, int] = {}
        proctor_ids: Dict[str, int] = {}

        schools_schema = is_schools_schema_ready(cursor)

        def emit_progress(message: str) -> None:
            if progress_callback:
                progress_callback(
                    {
                        "processed_count": processed,
                        "successful": successful,
                        "skipped": skipped,
                        "failed": 0,
                        "message": message,
                        "entities_created": entities,
                    }
                )

        # 1. Schools
        if not schools_schema:
            for school in preview.get("schools") or []:
                if school.get("action") == "use_existing":
                    school_ids[school["key"]] = int(school["existing_id"])

        for school in (preview.get("schools") or []) if schools_schema else []:
            key = school["key"]
            if school.get("action") == "use_existing":
                school_ids[key] = int(school["existing_id"])
                continue
            cursor.execute(
                "INSERT INTO schools (name, short_name, notes) VALUES (%s, NULL, NULL)",
                (school["name"],),
            )
            school_id = cursor.lastrowid
            school_ids[key] = school_id
            entities["school_ids_created"].append(school_id)
        conn.commit()
        emit_progress("Школы обработаны")

        # 2. Groups
        for group in preview.get("groups") or []:
            key = group["key"]
            if group.get("action") == "use_existing":
                group_ids[key] = int(group["existing_id"])
                continue
            cursor.execute("INSERT INTO `groups` (name) VALUES (%s)", (group["name"],))
            group_id = cursor.lastrowid
            group_ids[key] = group_id
            entities["group_ids_created"].append(group_id)
        conn.commit()
        emit_progress("Группы созданы")

        # 3. Proctors
        for proctor in preview.get("proctors") or []:
            key = proctor["key"]
            group_key = proctor.get("group_key")
            target_group_id = group_ids.get(group_key) if group_key else None

            if proctor.get("action") == "use_existing":
                proctor_id = int(proctor["existing_id"])
                proctor_ids[key] = proctor_id
                if target_group_id:
                    cursor.execute(
                        "SELECT group_id FROM proctors WHERE id = %s",
                        (proctor_id,),
                    )
                    existing = cursor.fetchone()
                    existing_group_id = existing.get("group_id") if existing else None
                    if existing_group_id:
                        target_group_id = existing_group_id
                continue

            person = normalize_person_name(proctor.get("full_name"))
            if not person:
                raise RuntimeError(f"Некорректное ФИО проктора: {proctor.get('full_name')}")

            cursor.execute(
                "INSERT INTO proctors (full_name, group_id) VALUES (%s, %s)",
                (proctor["full_name"], target_group_id),
            )
            proctor_id = cursor.lastrowid
            proctor_ids[key] = proctor_id
            entities["proctor_ids_created"].append(proctor_id)

            login = _generate_proctor_login(cursor, person["first_name"], person["last_name"])
            password = _generate_password()
            cursor.execute(
                """
                INSERT INTO auth_users (username, password, ref_id, role)
                VALUES (%s, %s, %s, 'proctor')
                """,
                (login, generate_password_hash(password), proctor_id),
            )
            entities["auth_usernames_created"].append(login)
        conn.commit()
        emit_progress("Прокторы созданы")

        total_to_process = len(students)

        # 4. Students
        for student in students:
            processed += 1
            base_row = {
                "row": student.get("row"),
                "full_name": student.get("full_name"),
                "class": student.get("class"),
                "school_name": student.get("school_name"),
                "proctor_name": student.get("proctor_name"),
                "group_name": None,
                "login": None,
                "password": None,
                "status": student.get("action"),
                "message": None,
            }

            if student.get("action") == "skip":
                skipped += 1
                base_row["status"] = "skipped"
                base_row["message"] = "Уже есть в системе"
                base_row["existing_student_id"] = student.get("existing_student_id")
                result_rows.append(base_row)
                emit_progress(f"Обработка учеников: {processed}/{total_to_process}")
                continue

            if student.get("action") == "error":
                raise RuntimeError(
                    f"Строка {student.get('row')}: "
                    f"{'; '.join(student.get('errors') or ['ошибка валидации'])}"
                )

            person = normalize_person_name(student.get("full_name"))
            if not person:
                raise RuntimeError(f"Строка {student.get('row')}: некорректное ФИО ученика")

            class_number = student.get("class")
            school_id = None
            school_key = student.get("school_key")
            if school_key and schools_schema:
                school_id = school_ids.get(school_key)

            group_id = None
            group_name = None
            proctor_key = student.get("proctor_key")
            if proctor_key:
                existing_proctor = proctors_map.get(proctor_key) or {}
                if existing_proctor.get("action") == "use_existing" and existing_proctor.get("existing_group_id"):
                    group_id = int(existing_proctor["existing_group_id"])
                    cursor.execute("SELECT name FROM `groups` WHERE id = %s", (group_id,))
                    group_row = cursor.fetchone()
                    group_name = group_row["name"] if group_row else None
                else:
                    group_key = student.get("group_key")
                    if group_key:
                        group_id = group_ids.get(group_key)
                        group_name = (groups_map.get(group_key) or {}).get("name")
            else:
                base_row["message"] = "Без группы"

            if schools_schema:
                cursor.execute(
                    """
                    INSERT INTO students (full_name, class, group_id, school_id, tg_name)
                    VALUES (%s, %s, %s, %s, NULL)
                    """,
                    (student["full_name"], class_number, group_id, school_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO students (full_name, class, group_id, tg_name)
                    VALUES (%s, %s, %s, NULL)
                    """,
                    (student["full_name"], class_number, group_id),
                )

            student_id = cursor.lastrowid
            entities["student_ids_created"].append(student_id)

            login = _generate_student_login(
                cursor,
                person["first_name"],
                person["last_name"],
                int(class_number),
            )
            password = _generate_password()
            cursor.execute(
                """
                INSERT INTO auth_users (username, password, ref_id, role)
                VALUES (%s, %s, %s, 'student')
                """,
                (login, generate_password_hash(password), student_id),
            )
            entities["auth_usernames_created"].append(login)
            conn.commit()

            successful += 1
            base_row.update(
                {
                    "status": "created",
                    "group_name": group_name,
                    "login": login,
                    "password": password,
                    "student_id": student_id,
                    "message": base_row.get("message") or "Создан",
                }
            )
            result_rows.append(base_row)
            emit_progress(f"Обработка учеников: {processed}/{total_to_process}")

        save_job_results(job_id, result_rows)
        message = f"Создано: {successful}, пропущено: {skipped}"
        _update_job(
            job_id,
            status="completed",
            completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            processed_count=processed,
            successful=successful,
            skipped=skipped,
            failed=0,
            message=message,
            entities_created=entities,
        )

    except Exception as exc:
        if conn:
            try:
                cursor = conn.cursor(dictionary=True)
                _update_job(job_id, status="rolling_back", message=f"Откат: {exc}")
                _rollback_entities(cursor, entities)
                conn.commit()
            except Exception as rollback_exc:
                conn.rollback()
                _update_job(
                    job_id,
                    status="failed",
                    completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                    message=f"Ошибка: {exc}. Откат не завершён: {rollback_exc}",
                    entities_created=entities,
                )
            else:
                _update_job(
                    job_id,
                    status="failed",
                    completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                    message=f"Импорт отменён: {exc}",
                    entities_created=entities,
                    processed_count=processed,
                    successful=0,
                    skipped=skipped,
                    failed=1,
                )
        else:
            _update_job(
                job_id,
                status="failed",
                completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                message=str(exc),
            )
    finally:
        if conn:
            close_db_connection(conn)
