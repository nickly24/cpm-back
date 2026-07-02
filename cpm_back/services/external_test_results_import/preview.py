"""Preview и валидация импорта результатов внешних тестов."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from cpm_back.services.exam.get_external_tests import (
    get_external_test_by_id,
    parse_external_test_id,
)
from cpm_back.services.user_import.person_name import normalize_person_name, person_key


IMPORT_TYPE = "external_test_results"


def _load_students_index(cursor) -> Dict[str, List[Dict[str, Any]]]:
    cursor.execute("SELECT id, full_name FROM students")
    index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in cursor.fetchall():
        key = person_key(row.get("full_name"))
        if key:
            index[key].append({"id": row["id"], "full_name": row["full_name"]})
    return index


def _load_existing_results(cursor, test_id: int) -> set[int]:
    cursor.execute(
        "SELECT student_id FROM test_sessions WHERE test_id = %s",
        (test_id,),
    )
    return {int(row["student_id"]) for row in cursor.fetchall() if row.get("student_id") is not None}


def _duplicate_rows_by_key(raw_rows: List[Dict[str, Any]]) -> Dict[str, List[int]]:
    rows_by_key: Dict[str, List[int]] = defaultdict(list)
    for raw in raw_rows:
        key = person_key(raw.get("full_name"))
        if key:
            rows_by_key[key].append(int(raw.get("row") or 0))
    return {key: rows for key, rows in rows_by_key.items() if len(rows) > 1}


def build_preview_from_rows(cursor, raw_rows: List[Dict[str, Any]], test_id: Any) -> Dict[str, Any]:
    numeric_test_id = parse_external_test_id(test_id)
    external_test = get_external_test_by_id(numeric_test_id)
    rows: List[Dict[str, Any]] = []

    if not numeric_test_id or not external_test:
        rows = [
            {
                **raw,
                "student_id": None,
                "student_full_name": None,
                "action": "error",
                "errors": ["Выбранный внешний тест не найден"],
            }
            for raw in raw_rows
        ]
        return _build_result(rows, test_id=None, external_test=None)

    students_index = _load_students_index(cursor)
    existing_results = _load_existing_results(cursor, numeric_test_id)
    duplicate_rows = _duplicate_rows_by_key(raw_rows)

    for raw in raw_rows:
        full_name = str(raw.get("full_name") or "").strip()
        errors: List[str] = []
        student_id: Optional[int] = None
        student_full_name: Optional[str] = None

        person = normalize_person_name(full_name)
        if not full_name:
            errors.append("Пустое ФИО")
        elif not person:
            errors.append("Некорректное ФИО: укажите минимум фамилию и имя")

        key = person["key"] if person else None
        if key and key in duplicate_rows:
            errors.append(f"Дубль ФИО в файле: строки {', '.join(str(r) for r in duplicate_rows[key])}")

        if key and key not in duplicate_rows:
            matches = students_index.get(key) or []
            if not matches:
                errors.append("Студент не найден в CPM")
            elif len(matches) > 1:
                ids = ", ".join(str(match["id"]) for match in matches)
                errors.append(f"В CPM найдено несколько студентов: {ids}")
            else:
                student_id = int(matches[0]["id"])
                student_full_name = matches[0]["full_name"]
                if student_id in existing_results:
                    errors.append("У студента уже есть результат по выбранному внешнему тесту")

        percent = raw.get("percent")
        if percent is None:
            errors.append("Процент правильных ответов пустой или не является числом")
        elif percent < 0 or percent > 100:
            errors.append("Процент правильных ответов должен быть от 0 до 100")

        rows.append(
            {
                **raw,
                "student_id": student_id,
                "student_full_name": student_full_name,
                "action": "error" if errors else "import",
                "errors": errors,
            }
        )

    return _build_result(rows, test_id=numeric_test_id, external_test=external_test)


def _build_result(
    rows: List[Dict[str, Any]],
    *,
    test_id: Optional[int],
    external_test: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    errors_count = sum(1 for row in rows if row.get("errors"))
    import_rows = sum(1 for row in rows if row.get("action") == "import")
    duplicate_rows = sum(
        1
        for row in rows
        if any(str(error).startswith("Дубль ФИО") for error in row.get("errors") or [])
    )
    existing_results = sum(
        1
        for row in rows
        if "У студента уже есть результат по выбранному внешнему тесту" in (row.get("errors") or [])
    )
    summary = {
        "total_rows": len(rows),
        "import_rows": import_rows,
        "row_errors": errors_count,
        "matched_students": import_rows,
        "duplicate_rows": duplicate_rows,
        "existing_results": existing_results,
    }
    return {
        "test_id": test_id,
        "test": external_test,
        "rows": rows,
        "summary": summary,
    }


def validate_preview_for_commit(preview: Dict[str, Any]) -> Optional[str]:
    summary = preview.get("summary") or {}
    if not preview.get("test_id"):
        return "Выбранный внешний тест не найден"
    if int(summary.get("row_errors") or 0) > 0:
        return "Исправьте ошибки в строках перед загрузкой"
    if int(summary.get("import_rows") or 0) <= 0:
        return "Нет строк для загрузки"
    return None

