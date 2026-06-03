def fetch_school_row(cursor, school_id):
    cursor.execute(
        """
        SELECT id, name, short_name, notes, is_active, created_at, updated_at
        FROM schools
        WHERE id = %s
        """,
        (school_id,),
    )
    return cursor.fetchone()


def serialize_school(row, student_count=None):
    if not row:
        return None

    payload = {
        "school_id": row["id"],
        "name": row["name"],
        "short_name": row.get("short_name"),
        "notes": row.get("notes"),
        "is_active": bool(row.get("is_active", 1)),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }

    if student_count is not None:
        payload["student_count"] = student_count

    return payload


def validate_school_id(cursor, school_id, *, require_active=True):
    if school_id is None:
        return {"status": True, "school": None}

    try:
        school_id = int(school_id)
    except (TypeError, ValueError):
        return {"status": False, "error": "school_id должен быть числом"}

    row = fetch_school_row(cursor, school_id)
    if not row:
        return {"status": False, "error": f"Школа с ID {school_id} не найдена"}

    if require_active and not row.get("is_active", 1):
        return {"status": False, "error": "Школа деактивирована"}

    return {"status": True, "school": row}
