"""Нормализация ФИО: сравнение по фамилии и имени, отчество отбрасывается."""
from __future__ import annotations

from typing import Any, Dict, Optional


def normalize_person_name(full_name: Optional[str]) -> Optional[Dict[str, Any]]:
    if not full_name or not str(full_name).strip():
        return None

    parts = [part for part in str(full_name).strip().split() if part]
    if len(parts) < 2:
        return None

    last_name = parts[0]
    first_name = parts[1]
    display = str(full_name).strip()

    return {
        "display": display,
        "first_name": first_name,
        "last_name": last_name,
        "key": f"{last_name.lower()}|{first_name.lower()}",
    }


def person_key(full_name: Optional[str]) -> Optional[str]:
    parsed = normalize_person_name(full_name)
    return parsed["key"] if parsed else None
