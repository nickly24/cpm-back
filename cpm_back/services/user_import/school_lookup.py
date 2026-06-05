"""Поиск школ с учётом опциональной схемы."""
from __future__ import annotations

from typing import Any, Dict


def is_schools_schema_ready(cursor) -> bool:
    cursor.execute("SHOW TABLES LIKE 'schools'")
    if not cursor.fetchone():
        return False
    cursor.execute("SHOW COLUMNS FROM students LIKE 'school_id'")
    return cursor.fetchone() is not None


def load_schools_index(cursor) -> Dict[str, Dict[str, Any]]:
    if not is_schools_schema_ready(cursor):
        return {}

    cursor.execute("SELECT id, name FROM schools")
    index: Dict[str, Dict[str, Any]] = {}
    for row in cursor.fetchall():
        key = str(row.get("name") or "").strip().lower()
        if key and key not in index:
            index[key] = {"id": row["id"], "name": row["name"]}
    return index
