import random
import string

from werkzeug.security import generate_password_hash


STAFF_ROLES = ("proctor", "examinator", "supervisor")

ROLE_TABLES = {
    "proctor": "proctors",
    "examinator": "examinators",
    "supervisor": "supervisors",
}

LOGIN_PREFIX = {
    "proctor": "pr",
    "examinator": "ex",
    "supervisor": "sv",
}


def is_password_hashed(value: str | None) -> bool:
    if not value:
        return False
    return value.startswith(("pbkdf2:", "scrypt:", "argon2:"))


def expose_password(value: str | None) -> str | None:
    if not value or is_password_hashed(value):
        return None
    return value


def generate_password() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=8))


def parse_person_name(full_name: str) -> tuple[str, str] | None:
    parts = full_name.strip().split()
    if len(parts) < 2:
        return None
    return parts[0], parts[-1]


def generate_staff_login(cursor, role: str, first_name: str, last_name: str) -> str:
    prefix = LOGIN_PREFIX[role]
    base_login = f"{prefix}{first_name[0].lower()}{last_name.lower()}"
    login = base_login
    counter = 1
    while True:
        cursor.execute("SELECT 1 FROM auth_users WHERE username = %s", (login,))
        if not cursor.fetchone():
            return login
        login = f"{base_login}{counter}"
        counter += 1


def hash_password(password: str) -> str:
    return generate_password_hash(password)
