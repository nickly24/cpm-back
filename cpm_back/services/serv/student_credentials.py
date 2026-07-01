from __future__ import annotations

import random
import string


def parse_student_name(full_name: str) -> tuple[str, str] | None:
    parts = [part for part in str(full_name or "").strip().split() if part]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def generate_student_login(cursor, full_name: str) -> str | None:
    parsed = parse_student_name(full_name)
    if not parsed:
        return None

    last_name, first_name = parsed
    base_login = f"{last_name[:4].lower()}{first_name[:2].lower()}"

    while True:
        suffix = "".join(random.choices(string.digits, k=3))
        login = f"{base_login}{suffix}"
        cursor.execute("SELECT 1 FROM auth_users WHERE username = %s", (login,))
        if not cursor.fetchone():
            return login
