"""Сборка preview-модели и разрешение действий create / use_existing / skip."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from cpm_back.services.user_import.person_name import normalize_person_name, person_key

from .school_lookup import load_schools_index


def _school_key(name: str) -> str:
    return str(name or "").strip().lower()


def _parse_class(raw: str) -> Tuple[Optional[int], Optional[str]]:
    text = str(raw or "").strip()
    if not text:
        return None, "Не указан класс"
    try:
        value = int(float(text))
    except (TypeError, ValueError):
        return None, "Класс должен быть числом 9, 10 или 11"
    if value not in (9, 10, 11):
        return None, "Класс должен быть 9, 10 или 11"
    return value, None


def _load_students_index(cursor) -> Dict[str, Dict[str, Any]]:
    cursor.execute("SELECT id, full_name FROM students")
    index: Dict[str, Dict[str, Any]] = {}
    for row in cursor.fetchall():
        key = person_key(row.get("full_name"))
        if key and key not in index:
            index[key] = {"id": row["id"], "full_name": row["full_name"]}
    return index


def _load_proctors_index(cursor) -> Dict[str, Dict[str, Any]]:
    cursor.execute("SELECT id, full_name, group_id FROM proctors")
    index: Dict[str, Dict[str, Any]] = {}
    for row in cursor.fetchall():
        key = person_key(row.get("full_name"))
        if key and key not in index:
            index[key] = {
                "id": row["id"],
                "full_name": row["full_name"],
                "group_id": row.get("group_id"),
            }
    return index


def _load_groups_index(cursor) -> Dict[str, Dict[str, Any]]:
    cursor.execute("SELECT id, name FROM `groups`")
    index: Dict[str, Dict[str, Any]] = {}
    for row in cursor.fetchall():
        key = str(row.get("name") or "").strip().lower()
        if key and key not in index:
            index[key] = {"id": row["id"], "name": row["name"]}
    return index


def build_preview_from_rows(cursor, raw_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    students_index = _load_students_index(cursor)
    proctors_index = _load_proctors_index(cursor)
    groups_index = _load_groups_index(cursor)
    schools_index = load_schools_index(cursor)

    schools: Dict[str, Dict[str, Any]] = {}
    proctors: Dict[str, Dict[str, Any]] = {}
    groups: Dict[str, Dict[str, Any]] = {}
    students: List[Dict[str, Any]] = []
    row_errors = 0

    for raw in raw_rows:
        row_no = raw.get("row")
        full_name = str(raw.get("full_name") or "").strip()
        class_number, class_error = _parse_class(raw.get("class_raw", ""))
        school_name = str(raw.get("school_name") or "").strip()
        proctor_name = str(raw.get("proctor_name") or "").strip()

        errors: List[str] = []
        if class_error:
            errors.append(class_error)

        person = normalize_person_name(full_name)
        if not person:
            errors.append("Укажите фамилию и имя ученика")

        school_key = None
        if school_name:
            school_key = _school_key(school_name)
            if school_key not in schools:
                existing = schools_index.get(school_key)
                schools[school_key] = {
                    "key": school_key,
                    "name": school_name,
                    "action": "use_existing" if existing else "create",
                    "existing_id": existing["id"] if existing else None,
                }

        proctor_key = None
        group_key = None
        if proctor_name:
            proctor_person = normalize_person_name(proctor_name)
            if not proctor_person:
                errors.append("У проктора укажите фамилию и имя")
            else:
                proctor_key = proctor_person["key"]
                group_key = proctor_key
                group_name = proctor_person["display"]
                group_name_key = group_name.strip().lower()

                if group_key not in groups:
                    existing_group = groups_index.get(group_name_key)
                    existing_proctor = proctors_index.get(proctor_key)
                    groups[group_key] = {
                        "key": group_key,
                        "name": group_name,
                        "proctor_key": proctor_key,
                        "action": "use_existing" if existing_group else "create",
                        "existing_id": existing_group["id"] if existing_group else None,
                        "student_count": 0,
                    }

                if proctor_key not in proctors:
                    proctors[proctor_key] = {
                        "key": proctor_key,
                        "full_name": proctor_person["display"],
                        "group_key": group_key,
                        "action": "use_existing" if proctors_index.get(proctor_key) else "create",
                        "existing_id": (
                            proctors_index[proctor_key]["id"]
                            if proctors_index.get(proctor_key)
                            else None
                        ),
                        "existing_group_id": (
                            proctors_index[proctor_key].get("group_id")
                            if proctors_index.get(proctor_key)
                            else None
                        ),
                        "student_count": 0,
                    }

        action = "create"
        skip_reason = None
        existing_student_id = None
        if person and person["key"] in students_index:
            action = "skip"
            skip_reason = "already_exists"
            existing_student_id = students_index[person["key"]]["id"]

        if errors:
            action = "error"
            row_errors += 1

        student_item = {
            "row": row_no,
            "full_name": full_name,
            "person_key": person["key"] if person else None,
            "class": class_number,
            "school_key": school_key,
            "school_name": school_name or None,
            "proctor_key": proctor_key,
            "proctor_name": proctor_name or None,
            "group_key": group_key,
            "action": action,
            "skip_reason": skip_reason,
            "existing_student_id": existing_student_id,
            "errors": errors,
            "without_group": not proctor_name,
        }
        students.append(student_item)

        if group_key and group_key in groups and action in ("create", "skip"):
            groups[group_key]["student_count"] = int(groups[group_key].get("student_count") or 0) + 1
        if proctor_key and proctor_key in proctors and action in ("create", "skip"):
            proctors[proctor_key]["student_count"] = int(proctors[proctor_key].get("student_count") or 0) + 1

    summary = _build_summary(schools, groups, proctors, students, row_errors)
    return {
        "schools": list(schools.values()),
        "groups": list(groups.values()),
        "proctors": list(proctors.values()),
        "students": students,
        "summary": summary,
    }


def _build_summary(
    schools: Dict[str, Dict[str, Any]],
    groups: Dict[str, Dict[str, Any]],
    proctors: Dict[str, Dict[str, Any]],
    students: List[Dict[str, Any]],
    row_errors: int,
) -> Dict[str, Any]:
    create_students = sum(1 for s in students if s.get("action") == "create")
    skip_students = sum(1 for s in students if s.get("action") == "skip")
    without_group = sum(
        1 for s in students if s.get("action") == "create" and s.get("without_group")
    )

    return {
        "total_rows": len(students),
        "row_errors": row_errors,
        "schools_total": len(schools),
        "schools_create": sum(1 for s in schools.values() if s.get("action") == "create"),
        "schools_existing": sum(1 for s in schools.values() if s.get("action") == "use_existing"),
        "groups_total": len(groups),
        "groups_create": sum(1 for g in groups.values() if g.get("action") == "create"),
        "groups_existing": sum(1 for g in groups.values() if g.get("action") == "use_existing"),
        "proctors_total": len(proctors),
        "proctors_create": sum(1 for p in proctors.values() if p.get("action") == "create"),
        "proctors_existing": sum(1 for p in proctors.values() if p.get("action") == "use_existing"),
        "students_create": create_students,
        "students_skip": skip_students,
        "students_without_group": without_group,
    }


def rebuild_preview_actions(cursor, preview: Dict[str, Any]) -> Dict[str, Any]:
    """Пересчитать action/use_existing/skip после правок preview на фронте."""
    raw_rows = []
    for student in preview.get("students") or []:
        raw_rows.append(
            {
                "row": student.get("row"),
                "full_name": student.get("full_name"),
                "class_raw": student.get("class"),
                "school_name": student.get("school_name") or "",
                "proctor_name": student.get("proctor_name") or "",
            }
        )
    return build_preview_from_rows(cursor, raw_rows)


def validate_preview_for_commit(preview: Dict[str, Any]) -> Optional[str]:
    summary = preview.get("summary") or {}
    if int(summary.get("row_errors") or 0) > 0:
        return "Исправьте ошибки в строках перед загрузкой"
    students = preview.get("students") or []
    if not students:
        return "Нет строк для загрузки"
    if not any(student.get("action") == "create" for student in students):
        return "Нет новых учеников для создания (все строки пропускаются)"
    return None
