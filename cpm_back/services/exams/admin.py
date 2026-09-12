"""Administrative domain operations. Caller owns the exclusive exam lock."""

from .common import (
    fail,
    fields,
    integer,
    decimal_number,
    text_value,
    enum_value,
    parse_date,
    parse_datetime,
    iso,
    now,
    check_version,
    page_args,
    search_value,
    pagination,
    digest,
    bump_config,
    invalidate_rating,
)
from .scoring import calculate_max_score, validate_thresholds, build_grade_ranges

CONFIG_COLUMNS = {
    "startAt": "start_at",
    "endAt": "end_at",
    "fractionalMode": "fractional_mode",
    "tieBreakerSourceMode": "tie_breaker_source_mode",
    "tieBreakerPartId": "tie_breaker_part_id",
    "tieBreakerHalfMode": "tie_breaker_half_mode",
}
PART_COLUMNS = {
    "code": "code",
    "questionWeight": "question_weight",
    "questionCount": "question_count",
}


def require_row(db, table, row_id, exam_id, code):
    row = db.one(f"SELECT * FROM {table} WHERE id=%s AND exam_id=%s", (row_id, exam_id))
    if not row:
        fail(code, "Запись не найдена", 404)
    return row


def get_exam(db, exam_id):
    row = db.one(
        """SELECT e.*,d.name direction_name,s.start_at,s.end_at
        FROM exams e LEFT JOIN directions d ON d.id=e.direction_id
        LEFT JOIN classic_exam_settings s ON s.exam_id=e.id WHERE e.id=%s""",
        (exam_id,),
    )
    if not row:
        fail("exam_not_found", "Экзамен не найден", 404)
    return exam_dto(
        row, readiness(db, exam_id) if row["exam_type"] == "classic" else None
    )


def exam_dto(row, report=None):
    result = {
        "id": row["id"],
        "examType": row["exam_type"],
        "directionId": row["direction_id"],
        "directionName": row["direction_name"] or row["name"] or "",
        "legacyName": row["name"] if row["direction_id"] is None else None,
        "date": iso(row["date"]),
        "startAt": iso(row["start_at"]),
        "endAt": iso(row["end_at"]),
        "createdAt": iso(row["created_at"]),
        "updatedAt": iso(row["updated_at"]),
        "version": row["version"],
        "readiness": "not_applicable",
    }
    if row["exam_type"] == "classic":
        result["readiness"] = (
            "ready" if report and report["isConfigured"] else "incomplete"
        )
    return result


def list_exams(db, args):
    page, limit, offset = page_args(args)
    kind = enum_value(
        args.get("type", "all"), ("all", "classic", "outside_lms"), "exam_type"
    )
    where, params = ["1=1"], []
    if kind != "all":
        where.append("e.exam_type=%s")
        params.append(kind)
    if args.get("directionId"):
        from .common import query_int

        where.append("e.direction_id=%s")
        params.append(query_int(args["directionId"]))
    if args.get("search"):
        where.append("COALESCE(d.name,e.name) LIKE %s ESCAPE '!'")
        params.append(search_value(args))
    date_sql = "CASE WHEN e.exam_type='classic' THEN DATE(DATE_ADD(s.start_at,INTERVAL 3 HOUR)) ELSE e.date END"
    for key, sign in (("dateFrom", ">="), ("dateTo", "<=")):
        if args.get(key):
            where.append(f"{date_sql}{sign}%s")
            params.append(parse_date(args[key]))
    joins = "FROM exams e LEFT JOIN directions d ON d.id=e.direction_id LEFT JOIN classic_exam_settings s ON s.exam_id=e.id"
    condition = " AND ".join(where)
    order = {
        "date_desc": date_sql + " DESC,e.id DESC",
        "date_asc": date_sql + ",e.id",
        "direction_asc": "COALESCE(d.name,e.name),e.id",
        "created_desc": "e.created_at DESC,e.id DESC",
    }
    sort = enum_value(args.get("sort", "date_desc"), order, "sort")
    total = db.scalar(f"SELECT COUNT(*) {joins} WHERE {condition}", params)
    rows = db.all(
        f"SELECT e.*,d.name direction_name,s.start_at,s.end_at {joins} WHERE {condition} ORDER BY {order[sort]} LIMIT %s OFFSET %s",
        params + [limit, offset],
    )
    bundles = readiness_bundles(
        db, [r["id"] for r in rows if r["exam_type"] == "classic"]
    )
    return {
        "items": [
            exam_dto(
                r,
                (
                    readiness(db, r["id"], bundle=bundles[r["id"]])
                    if r["exam_type"] == "classic"
                    else None
                ),
            )
            for r in rows
        ],
        "pagination": pagination(total, page, limit),
    }


def create_exam(db, payload):
    fields(payload, ("examType", "directionId", "date"), ("examType", "directionId"))
    kind = enum_value(payload["examType"], ("outside_lms", "classic"), "exam_type")
    direction = integer(payload["directionId"], "direction_id")
    if not db.one("SELECT id FROM directions WHERE id=%s", (direction,)):
        fail("direction_not_found", "Направление не найдено", 404)
    if kind == "classic" and "date" in payload:
        fail("unknown_field", fields=["date"])
    exam_id = db.insert(
        "exams",
        {
            "exam_type": kind,
            "direction_id": direction,
            "date": parse_date(payload.get("date")) if kind == "outside_lms" else None,
            "name": None,
        },
    )
    if kind == "classic":
        db.insert("classic_exam_settings", {"exam_id": exam_id})
    invalidate_rating(db)
    return get_exam(db, exam_id)


def update_direction(db, exam, payload):
    fields(
        payload, ("directionId", "expectedVersion"), ("directionId", "expectedVersion")
    )
    check_version(exam, payload["expectedVersion"], "exam_modified")
    direction = integer(payload["directionId"], "direction_id")
    if not db.one("SELECT id FROM directions WHERE id=%s", (direction,)):
        fail("direction_not_found", status=404)
    if direction != exam["direction_id"]:
        db.update(
            "exams",
            {"direction_id": direction, "version": exam["version"] + 1},
            "id=%s",
            (exam["id"],),
        )
        if exam["exam_type"] == "classic":
            bump_config(db, exam["id"])
        invalidate_rating(db)
    return get_exam(db, exam["id"])


def get_config_row(db, exam_id):
    row = db.one("SELECT * FROM classic_exam_settings WHERE exam_id=%s", (exam_id,))
    if not row:
        fail("settings_missing", "Конфигурация не инициализирована", 500)
    return row


def config_dto(row):
    result = {
        key: iso(row[column]) if key in ("startAt", "endAt") else row[column]
        for key, column in CONFIG_COLUMNS.items()
    }
    result.update(
        {
            "examId": row["exam_id"],
            "configVersion": row["config_version"],
            "updatedAt": iso(row["updated_at"]),
        }
    )
    return result


def config_errors(row):
    errors = []
    for column, field, label in (
        ("start_at", "startAt", "начало"),
        ("end_at", "endAt", "окончание"),
        ("fractional_mode", "fractionalMode", "правило спорного балла"),
    ):
        if row.get(column) is None:
            errors.append(
                {
                    "code": column + "_required",
                    "field": field,
                    "message": "Укажите " + label,
                    "details": {},
                }
            )
    if row.get("fractional_mode") == "extra_question":
        for column, field in (
            ("tie_breaker_source_mode", "tieBreakerSourceMode"),
            ("tie_breaker_half_mode", "tieBreakerHalfMode"),
        ):
            if row.get(column) is None:
                errors.append(
                    {
                        "code": column + "_required",
                        "field": field,
                        "message": "Настройте дополнительный вопрос",
                        "details": {},
                    }
                )
        if (
            row.get("tie_breaker_source_mode") == "specific_part"
            and row.get("tie_breaker_part_id") is None
        ):
            errors.append(
                {
                    "code": "tie_breaker_part_required",
                    "field": "tieBreakerPartId",
                    "message": "Выберите часть дополнительного вопроса",
                    "details": {},
                }
            )
    return errors


def get_config(db, exam_id):
    row = get_config_row(db, exam_id)
    return {"config": config_dto(row), "localValidation": config_errors(row)}


def merge_config(db, exam_id, row, payload):
    result = dict(row)
    for key, column in CONFIG_COLUMNS.items():
        if key not in payload:
            continue
        value = payload[key]
        if key in ("startAt", "endAt"):
            value = parse_datetime(value)
        elif key == "fractionalMode":
            value = enum_value(
                value,
                ("round_up", "round_down", "extra_question"),
                "fractional_mode",
                True,
            )
        elif key == "tieBreakerSourceMode":
            value = enum_value(
                value, ("specific_part", "any_part"), "tie_breaker_source_mode", True
            )
        elif key == "tieBreakerHalfMode":
            value = enum_value(
                value,
                ("repeat", "round_up", "round_down"),
                "tie_breaker_half_mode",
                True,
            )
        elif value is not None:
            value = integer(value, "part_id")
            require_row(db, "classic_exam_parts", value, exam_id, "part_not_found")
        result[column] = value
    if (
        result["start_at"]
        and result["end_at"]
        and result["end_at"] <= result["start_at"]
    ):
        fail("invalid_time_window", "Окончание должно быть позже начала")
    if result["fractional_mode"] != "extra_question":
        for key in (
            "tie_breaker_source_mode",
            "tie_breaker_part_id",
            "tie_breaker_half_mode",
        ):
            result[key] = None
    if result["tie_breaker_source_mode"] == "any_part":
        result["tie_breaker_part_id"] = None
    return result


def update_config(db, exam_id, payload):
    fields(
        payload,
        tuple(CONFIG_COLUMNS) + ("expectedConfigVersion",),
        ("expectedConfigVersion",),
    )
    row = get_config_row(db, exam_id)
    check_version(
        row, payload["expectedConfigVersion"], "config_modified", "config_version"
    )
    merged = merge_config(db, exam_id, row, payload)
    changes = {
        column: merged[column]
        for column in CONFIG_COLUMNS.values()
        if merged[column] != row[column]
    }
    if changes:
        changes["config_version"] = row["config_version"] + 1
        db.update("classic_exam_settings", changes, "exam_id=%s", (exam_id,))
        if "start_at" in changes or "end_at" in changes:
            invalidate_rating(db)
    return get_config(db, exam_id)


def part_rows(db, exam_id):
    return db.all(
        """SELECT p.*,COUNT(q.id) bank_size FROM classic_exam_parts p
        LEFT JOIN classic_exam_questions q ON q.part_id=p.id WHERE p.exam_id=%s
        GROUP BY p.id ORDER BY p.sort_order,p.id""",
        (exam_id,),
    )


def part_dto(row):
    return {
        "id": row["id"],
        "code": row["code"],
        "questionWeight": row["question_weight"],
        "questionCount": row["question_count"],
        "bankSize": row.get("bank_size", 0),
        "sortOrder": row["sort_order"],
        "version": row["version"],
        "updatedAt": iso(row["updated_at"]),
    }


def get_part(db, exam_id, part_id):
    row = require_row(db, "classic_exam_parts", part_id, exam_id, "part_not_found")
    row["bank_size"] = db.scalar(
        "SELECT COUNT(*) FROM classic_exam_questions WHERE part_id=%s", (part_id,)
    )
    return part_dto(row)


def normalize_part_payload(payload):
    result = dict(payload)
    for alias, canonical in (("weight", "questionWeight"), ("quota", "questionCount")):
        if alias in result:
            if canonical in result:
                fail("duplicate_field", fields=[alias, canonical])
            result[canonical] = result.pop(alias)
    return result


def save_part(db, exam_id, payload, part_id=None):
    payload = normalize_part_payload(payload)
    fields(
        payload,
        tuple(PART_COLUMNS) + ("expectedVersion",),
        ("expectedVersion",) if part_id else ("code",),
    )
    row = (
        require_row(db, "classic_exam_parts", part_id, exam_id, "part_not_found")
        if part_id
        else None
    )
    if row:
        check_version(row, payload["expectedVersion"], "part_modified")
    values = {}
    for key, column in PART_COLUMNS.items():
        if key not in payload:
            continue
        value = payload[key]
        if key == "code":
            value = text_value(value, "part_code", 1).upper()
            if value not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                fail("invalid_part_code")
            duplicate = db.one(
                "SELECT id FROM classic_exam_parts WHERE exam_id=%s AND code=%s",
                (exam_id, value),
            )
            if duplicate and duplicate["id"] != part_id:
                fail("part_code_conflict", "Эта буква уже используется", 409)
        elif value is not None:
            value = integer(
                value,
                "weight" if key == "questionWeight" else "question_count",
                1,
                1000 if key == "questionWeight" else 100,
            )
        values[column] = value
    if row:
        changes = {k: v for k, v in values.items() if row[k] != v}
        if changes:
            db.update(
                "classic_exam_parts",
                dict(changes, version=row["version"] + 1),
                "id=%s",
                (part_id,),
            )
            bump_config(db, exam_id)
    else:
        values.update(
            {
                "exam_id": exam_id,
                "sort_order": db.scalar(
                    "SELECT COALESCE(MAX(sort_order),0)+1 FROM classic_exam_parts WHERE exam_id=%s",
                    (exam_id,),
                ),
            }
        )
        part_id = db.insert("classic_exam_parts", values)
        bump_config(db, exam_id)
    return get_part(db, exam_id, part_id)


def question_dto(row):
    return {
        "id": row["id"],
        "partId": row["part_id"],
        "partCode": row["part_code"],
        "questionText": row["question_text"],
        "answerText": row["answer_text"],
        "sortOrder": row["sort_order"],
        "version": row["version"],
        "updatedAt": iso(row["updated_at"]),
    }


def get_question(db, exam_id, question_id):
    row = db.one(
        """SELECT q.*,p.code part_code FROM classic_exam_questions q
        JOIN classic_exam_parts p ON p.id=q.part_id WHERE q.exam_id=%s AND q.id=%s""",
        (exam_id, question_id),
    )
    if not row:
        fail("question_not_found", status=404)
    return question_dto(row)


def list_questions(db, exam_id, args):
    from .common import query_int

    page, limit, offset = page_args(args)
    where, params = ["q.exam_id=%s"], [exam_id]
    if args.get("partId"):
        where.append("q.part_id=%s")
        params.append(query_int(args["partId"]))
    if args.get("search"):
        where.append(
            "(q.question_text LIKE %s ESCAPE '!' OR q.answer_text LIKE %s ESCAPE '!')"
        )
        params.extend([search_value(args)] * 2)
    enum_value(args.get("sort", "order_asc"), ("order_asc",), "sort")
    clause = " AND ".join(where)
    total = db.scalar(
        "SELECT COUNT(*) FROM classic_exam_questions q WHERE " + clause, params
    )
    rows = db.all(
        "SELECT q.*,p.code part_code FROM classic_exam_questions q JOIN classic_exam_parts p ON p.id=q.part_id WHERE "
        + clause
        + " ORDER BY p.sort_order,q.sort_order,q.id LIMIT %s OFFSET %s",
        params + [limit, offset],
    )
    return {
        "items": [question_dto(r) for r in rows],
        "pagination": pagination(total, page, limit),
    }


def save_question(db, exam_id, payload, question_id=None):
    fields(
        payload,
        ("partId", "questionText", "answerText", "expectedVersion"),
        (
            ("expectedVersion",)
            if question_id
            else ("partId", "questionText", "answerText")
        ),
    )
    row = (
        require_row(
            db, "classic_exam_questions", question_id, exam_id, "question_not_found"
        )
        if question_id
        else None
    )
    if row:
        check_version(row, payload["expectedVersion"], "question_modified")
    values = (
        {k: row[k] for k in ("part_id", "question_text", "answer_text")} if row else {}
    )
    for key, column in (
        ("partId", "part_id"),
        ("questionText", "question_text"),
        ("answerText", "answer_text"),
    ):
        if key in payload:
            values[column] = (
                integer(payload[key], "part_id")
                if key == "partId"
                else text_value(payload[key], column, None, 61440)
            )
    require_row(db, "classic_exam_parts", values["part_id"], exam_id, "part_not_found")
    values["content_hash"] = digest([values["question_text"], values["answer_text"]])
    duplicate = db.one(
        "SELECT id FROM classic_exam_questions WHERE part_id=%s AND content_hash=%s",
        (values["part_id"], values["content_hash"]),
    )
    if duplicate and duplicate["id"] != question_id:
        fail(
            "question_duplicate",
            "Такой вопрос с этим ответом уже существует в части",
            409,
        )
    size = db.one(
        "SELECT COUNT(*) n,COALESCE(SUM(OCTET_LENGTH(question_text)+OCTET_LENGTH(answer_text)),0) bytes FROM classic_exam_questions WHERE exam_id=%s",
        (exam_id,),
    )
    total_bytes = (
        size["bytes"]
        + len(values["question_text"].encode())
        + len(values["answer_text"].encode())
    )
    if row:
        total_bytes -= len(row["question_text"].encode()) + len(
            row["answer_text"].encode()
        )
    if size["n"] + (0 if row else 1) > 5000 or total_bytes > 16 * 1024 * 1024:
        fail(
            "bank_capacity_exceeded",
            "Превышен размер банка вопросов",
            422,
            maxQuestions=5000,
            maxBytes=16 * 1024 * 1024,
        )
    if not row or values["part_id"] != row["part_id"]:
        values["sort_order"] = db.scalar(
            "SELECT COALESCE(MAX(sort_order),0)+1 FROM classic_exam_questions WHERE part_id=%s",
            (values["part_id"],),
        )
    if row:
        changes = {k: v for k, v in values.items() if row[k] != v}
        if changes:
            db.update(
                "classic_exam_questions",
                dict(changes, version=row["version"] + 1),
                "id=%s",
                (question_id,),
            )
            bump_config(db, exam_id)
    else:
        question_id = db.insert("classic_exam_questions", dict(values, exam_id=exam_id))
        bump_config(db, exam_id)
    return get_question(db, exam_id, question_id)


def thresholds(db, exam_id):
    return [
        {"grade": r["grade"], "minScore": r["min_score"]}
        for r in db.all(
            "SELECT grade,min_score FROM classic_exam_grade_thresholds WHERE exam_id=%s ORDER BY grade",
            (exam_id,),
        )
    ]


def get_scoring(db, exam_id):
    config = get_config_row(db, exam_id)
    maximum = calculate_max_score(part_rows(db, exam_id))
    rows = thresholds(db, exam_id)
    dto = {
        key: config[column]
        for key, column in CONFIG_COLUMNS.items()
        if key not in ("startAt", "endAt")
    }
    dto.update(
        {
            "minScore": 0,
            "maxScore": maximum,
            "thresholds": rows,
            "ranges": build_grade_ranges(maximum, rows),
            "configVersion": config["config_version"],
        }
    )
    return {"scoring": dto, "validation": validate_thresholds(maximum, rows)}


def save_scoring(db, exam_id, payload):
    allowed = (
        "thresholds",
        "fractionalMode",
        "tieBreakerSourceMode",
        "tieBreakerPartId",
        "tieBreakerHalfMode",
        "expectedConfigVersion",
    )
    fields(payload, allowed, ("thresholds", "fractionalMode", "expectedConfigVersion"))
    row = get_config_row(db, exam_id)
    check_version(
        row, payload["expectedConfigVersion"], "config_modified", "config_version"
    )
    merged = merge_config(
        db,
        exam_id,
        row,
        {key: payload.get(key) for key in allowed if key in CONFIG_COLUMNS},
    )
    errors = validate_thresholds(
        calculate_max_score(part_rows(db, exam_id)), payload["thresholds"]
    )
    errors += [
        e for e in config_errors(merged) if e["field"] not in ("startAt", "endAt")
    ]
    if errors:
        fail(errors[0]["code"], errors[0]["message"], 400, errors=errors)
    values = {
        column: merged[column]
        for key, column in CONFIG_COLUMNS.items()
        if key not in ("startAt", "endAt")
    }
    if thresholds(db, exam_id) != sorted(
        payload["thresholds"], key=lambda r: r["grade"]
    ) or any(values[k] != row[k] for k in values):
        db.execute(
            "DELETE FROM classic_exam_grade_thresholds WHERE exam_id=%s", (exam_id,)
        )
        for threshold in payload["thresholds"]:
            db.insert(
                "classic_exam_grade_thresholds",
                {
                    "exam_id": exam_id,
                    "grade": threshold["grade"],
                    "min_score": threshold["minScore"],
                },
            )
        db.update(
            "classic_exam_settings",
            dict(values, config_version=row["config_version"] + 1),
            "exam_id=%s",
            (exam_id,),
        )
    return get_scoring(db, exam_id)


def account(db, role, user_id, require_login=True):
    table = {"student": "students", "examinator": "examinators"}[role]
    row = db.one(
        f"""SELECT u.id,u.full_name,(SELECT MIN(username) FROM auth_users a WHERE a.role=%s AND a.ref_id=u.id) login
        FROM {table} u WHERE u.id=%s""",
        (role, user_id),
    )
    if not row or (require_login and row["login"] is None):
        fail(role + "_not_found", "Пользователь не найден или не имеет аккаунта", 404)
    return row


def lookups(db, role, args):
    table = {"student": "students", "examinator": "examinators"}[role]
    page, limit, offset = page_args(args)
    where, params = [], [role]
    if args.get("search"):
        where.append(
            "(u.full_name LIKE %s ESCAPE '!' OR CAST(u.id AS CHAR)=%s OR a.username LIKE %s ESCAPE '!')"
        )
        params.extend([search_value(args), args["search"], search_value(args)])
    clause = " AND ".join(where) if where else "1=1"
    joins = f"FROM {table} u LEFT JOIN auth_users a ON a.ref_id=u.id AND a.role=%s"
    total = db.scalar(f"SELECT COUNT(DISTINCT u.id) {joins} WHERE {clause}", params)
    rows = db.all(
        f"SELECT u.id,u.full_name,MIN(a.username) login {joins} WHERE {clause} GROUP BY u.id,u.full_name ORDER BY u.full_name,u.id LIMIT %s OFFSET %s",
        params + [limit, offset],
    )
    return {
        "items": [
            {"id": r["id"], "fullName": r["full_name"], "login": r["login"]}
            for r in rows
        ],
        "pagination": pagination(total, page, limit),
    }


def commission_members(db, commission_id):
    return db.all(
        """SELECT m.examinator_id id,e.full_name fullName,m.position FROM classic_exam_commission_members m
        JOIN examinators e ON e.id=m.examinator_id WHERE m.commission_id=%s ORDER BY m.position""",
        (commission_id,),
    )


def get_commission(db, exam_id, commission_id):
    row = require_row(
        db, "classic_exam_commissions", commission_id, exam_id, "commission_not_found"
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "version": row["version"],
        "updatedAt": iso(row["updated_at"]),
        "members": commission_members(db, commission_id),
        "assignedStudentsCount": db.scalar(
            "SELECT COUNT(DISTINCT student_id) FROM classic_exam_assignments WHERE source_commission_id=%s",
            (commission_id,),
        ),
    }


def save_commission(db, exam_id, payload, commission_id=None):
    fields(
        payload,
        ("name", "examinatorIds", "expectedVersion"),
        (
            ("name", "examinatorIds", "expectedVersion")
            if commission_id
            else ("examinatorIds",)
        ),
    )
    ids = payload["examinatorIds"]
    if not isinstance(ids, list) or not 1 <= len(ids) <= 6:
        fail(
            "invalid_commission_size", "В комиссии должно быть от 1 до 6 экзаменаторов"
        )
    ids = [integer(i, "examinator_id") for i in ids]
    if len(set(ids)) != len(ids):
        fail("duplicate_member")
    for ident in ids:
        account(db, "examinator", ident)
    row = (
        require_row(
            db,
            "classic_exam_commissions",
            commission_id,
            exam_id,
            "commission_not_found",
        )
        if commission_id
        else None
    )
    if row:
        check_version(row, payload["expectedVersion"], "commission_modified")
    name = payload.get("name")
    if name is None:
        n = db.scalar(
            "SELECT COALESCE(MAX(id),0)+1 FROM classic_exam_commissions WHERE exam_id=%s",
            (exam_id,),
        )
        name = f"Комиссия {n}"
        while db.one(
            "SELECT id FROM classic_exam_commissions WHERE exam_id=%s AND name=%s",
            (exam_id, name),
        ):
            n += 1
            name = f"Комиссия {n}"
    name = text_value(name, "commission_name", 120)
    duplicate = db.one(
        "SELECT id FROM classic_exam_commissions WHERE exam_id=%s AND name=%s",
        (exam_id, name),
    )
    if duplicate and duplicate["id"] != commission_id:
        fail("commission_name_conflict", status=409)
    if (
        row
        and row["name"] == name
        and [m["id"] for m in commission_members(db, commission_id)] == ids
    ):
        return get_commission(db, exam_id, commission_id)
    if row:
        db.update(
            "classic_exam_commissions",
            {"name": name, "version": row["version"] + 1},
            "id=%s",
            (commission_id,),
        )
        db.execute(
            "DELETE FROM classic_exam_commission_members WHERE commission_id=%s",
            (commission_id,),
        )
    else:
        commission_id = db.insert(
            "classic_exam_commissions", {"exam_id": exam_id, "name": name}
        )
    for pos, ident in enumerate(ids, 1):
        db.insert(
            "classic_exam_commission_members",
            {"commission_id": commission_id, "examinator_id": ident, "position": pos},
        )
    return get_commission(db, exam_id, commission_id)


def get_privilege(db, exam_id, student_id):
    account(db, "student", student_id, False)
    row = db.one(
        "SELECT * FROM classic_exam_student_privileges WHERE exam_id=%s AND student_id=%s",
        (exam_id, student_id),
    )
    return {
        "replacementLimit": row["replacement_limit"] if row else 0,
        "version": row["version"] if row else 0,
    }


def save_privilege(db, exam_id, student_id, payload):
    fields(
        payload,
        ("replacementLimit", "expectedVersion"),
        ("replacementLimit", "expectedVersion"),
    )
    limit = integer(payload["replacementLimit"], "replacement_limit", 0, 100)
    current = get_privilege(db, exam_id, student_id)
    check_version(current, payload["expectedVersion"], "privilege_modified")
    if not current["version"]:
        db.insert(
            "classic_exam_student_privileges",
            {"exam_id": exam_id, "student_id": student_id, "replacement_limit": limit},
        )
    elif current["replacementLimit"] != limit:
        db.update(
            "classic_exam_student_privileges",
            {"replacement_limit": limit, "version": current["version"] + 1},
            "exam_id=%s AND student_id=%s",
            (exam_id, student_id),
        )
    return get_privilege(db, exam_id, student_id)


def assignment_members(db, assignment_id):
    return db.all(
        "SELECT examinator_id id,full_name_snapshot fullName,position FROM classic_exam_assignment_members WHERE assignment_id=%s ORDER BY position",
        (assignment_id,),
    )


def get_assignment(db, exam_id, assignment_id):
    row = db.one(
        ASSIGNMENT_SELECT + " WHERE a.exam_id=%s AND a.id=%s", (exam_id, assignment_id)
    )
    if not row:
        fail("assignment_not_found", status=404)
    return assignment_dto(row, assignment_members(db, assignment_id))


ASSIGNMENT_SELECT = """SELECT a.*,s.full_name student_name,
    (SELECT MIN(username) FROM auth_users au WHERE au.role='student' AND au.ref_id=s.id) student_login,
    t.id attempt_id,t.status attempt_status,t.phase attempt_phase,
    COALESCE(p.replacement_limit,0) replacement_limit,COALESCE(p.version,0) privilege_version,
    f.history_generation effective_generation
    FROM classic_exam_assignments a JOIN students s ON s.id=a.student_id
    LEFT JOIN classic_exam_attempts t ON t.assignment_id=a.id
    LEFT JOIN classic_exam_student_privileges p ON p.exam_id=a.exam_id AND p.student_id=a.student_id
    JOIN classic_exam_assignments f ON f.exam_id=a.exam_id AND f.student_id=a.student_id AND f.attempt_no=1"""


def assignment_dto(row, members):
    return {
        "id": row["id"],
        "student": {
            "id": row["student_id"],
            "fullName": row["student_name"],
            "login": row["student_login"],
        },
        "attemptNo": row["attempt_no"],
        "sourceCommissionId": row["source_commission_id"],
        "commissionName": row["commission_name_snapshot"],
        "members": members,
        "replacementLimit": row["replacement_limit"],
        "privilegeVersion": row["privilege_version"],
        "status": row["attempt_status"] or "not_started",
        "attemptId": row["attempt_id"],
        "phase": row["attempt_phase"],
        "updatedAt": iso(row["updated_at"]),
        "version": row["version"],
        "historyGeneration": row["effective_generation"],
    }


def copy_assignment(
    db, exam_id, student_id, commission, actor, attempt_no=1, generation=1
):
    assignment_id = db.insert(
        "classic_exam_assignments",
        {
            "exam_id": exam_id,
            "student_id": student_id,
            "attempt_no": attempt_no,
            "source_commission_id": commission["id"],
            "commission_name_snapshot": commission["name"],
            "created_by_role": actor["role"],
            "created_by": actor["id"],
            "history_generation": generation,
        },
    )
    for member in commission["members"]:
        account(db, "examinator", member["id"])
        db.insert(
            "classic_exam_assignment_members",
            {
                "assignment_id": assignment_id,
                "examinator_id": member["id"],
                "position": member["position"],
                "full_name_snapshot": member["fullName"],
            },
        )
    return assignment_id


def save_assignment(db, exam_id, payload, actor, assignment_id=None):
    allowed = (
        (
            "commissionId",
            "replacementLimit",
            "expectedVersion",
            "expectedPrivilegeVersion",
        )
        if assignment_id
        else (
            "studentId",
            "commissionId",
            "replacementLimit",
            "expectedPrivilegeVersion",
        )
    )
    fields(
        payload,
        allowed,
        (
            ("commissionId", "expectedVersion")
            if assignment_id
            else ("studentId", "commissionId")
        ),
    )
    commission = get_commission(
        db, exam_id, integer(payload["commissionId"], "commission_id")
    )
    row = (
        require_row(
            db,
            "classic_exam_assignments",
            assignment_id,
            exam_id,
            "assignment_not_found",
        )
        if assignment_id
        else None
    )
    if row:
        check_version(row, payload["expectedVersion"], "assignment_modified")
        if db.one(
            "SELECT id FROM classic_exam_attempts WHERE assignment_id=%s",
            (assignment_id,),
        ):
            fail(
                "assignment_has_attempt",
                "Состав после подготовки сдачи менять нельзя",
                409,
            )
        student_id = row["student_id"]
    else:
        student_id = integer(payload["studentId"], "student_id")
        account(db, "student", student_id)
        if db.one(
            "SELECT id FROM classic_exam_assignments WHERE exam_id=%s AND student_id=%s AND attempt_no=1",
            (exam_id, student_id),
        ):
            fail("assignment_already_exists", status=409)
    if row and row["attempt_no"] == 2:
        first = db.all(
            """SELECT m.examinator_id FROM classic_exam_assignment_members m JOIN classic_exam_assignments a ON a.id=m.assignment_id
            WHERE a.exam_id=%s AND a.student_id=%s AND a.attempt_no=1""",
            (exam_id, student_id),
        )
        if {m["examinator_id"] for m in first} == {
            m["id"] for m in commission["members"]
        }:
            fail("retake_commission_unchanged", "Для пересдачи нужен новый состав", 422)
    if "replacementLimit" in payload:
        privilege = get_privilege(db, exam_id, student_id)
        expected = payload.get("expectedPrivilegeVersion", 0)
        save_privilege(
            db,
            exam_id,
            student_id,
            {
                "replacementLimit": payload["replacementLimit"],
                "expectedVersion": expected,
            },
        )
    if row:
        old_members = assignment_members(db, assignment_id)
        if (
            row["source_commission_id"] != commission["id"]
            or row["commission_name_snapshot"] != commission["name"]
            or old_members != commission["members"]
        ):
            db.update(
                "classic_exam_assignments",
                {
                    "source_commission_id": commission["id"],
                    "commission_name_snapshot": commission["name"],
                    "version": row["version"] + 1,
                },
                "id=%s",
                (assignment_id,),
            )
            db.execute(
                "DELETE FROM classic_exam_assignment_members WHERE assignment_id=%s",
                (assignment_id,),
            )
            for member in commission["members"]:
                account(db, "examinator", member["id"])
                db.insert(
                    "classic_exam_assignment_members",
                    {
                        "assignment_id": assignment_id,
                        "examinator_id": member["id"],
                        "position": member["position"],
                        "full_name_snapshot": member["fullName"],
                    },
                )
    else:
        assignment_id = copy_assignment(db, exam_id, student_id, commission, actor)
    return get_assignment(db, exam_id, assignment_id)


def list_assignments(db, exam_id, args, examiner_id=None):
    from .common import query_int

    page, limit, offset = page_args(args, 50)
    where, params = ["a.exam_id=%s"], [exam_id]
    if examiner_id:
        where.append(
            "EXISTS(SELECT 1 FROM classic_exam_assignment_members m WHERE m.assignment_id=a.id AND m.examinator_id=%s)"
        )
        params.append(examiner_id)
    if args.get("search"):
        where.append("(s.full_name LIKE %s ESCAPE '!' OR CAST(s.id AS CHAR)=%s)")
        params.extend([search_value(args), args["search"]])
    if args.get("commissionId"):
        where.append("a.source_commission_id=%s")
        params.append(query_int(args["commissionId"]))
    if args.get("status"):
        status = enum_value(
            args["status"],
            ("not_started", "pending_ready", "in_progress", "completed"),
            "status",
        )
        where.append("COALESCE(t.status,'not_started')=%s")
        params.append(status)
    joins = "FROM classic_exam_assignments a JOIN students s ON s.id=a.student_id LEFT JOIN classic_exam_attempts t ON t.assignment_id=a.id"
    clause = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) {joins} WHERE {clause}", params)
    rows = db.all(
        ASSIGNMENT_SELECT
        + f" WHERE {clause} ORDER BY s.full_name,s.id,a.attempt_no LIMIT %s OFFSET %s",
        params + [limit, offset],
    )
    members = {r["id"]: [] for r in rows}
    if members:
        markers = ",".join(["%s"] * len(members))
        for member in db.all(
            f"SELECT assignment_id,examinator_id id,full_name_snapshot fullName,position FROM classic_exam_assignment_members WHERE assignment_id IN ({markers}) ORDER BY assignment_id,position",
            list(members),
        ):
            members[member.pop("assignment_id")].append(member)
    return {
        "items": [assignment_dto(r, members[r["id"]]) for r in rows],
        "pagination": pagination(total, page, limit),
    }


def readiness_bundles(db, exam_ids):
    """Three bounded queries for any number of exam configuration reports."""
    if not exam_ids:
        return {}
    markers = ",".join(["%s"] * len(exam_ids))
    bundles = {i: {"parts": [], "thresholds": []} for i in exam_ids}
    for row in db.all(
        f"SELECT * FROM classic_exam_settings WHERE exam_id IN ({markers})", exam_ids
    ):
        bundles[row["exam_id"]]["config"] = row
    for row in db.all(
        f"""SELECT p.*,COUNT(q.id) bank_size FROM classic_exam_parts p
            LEFT JOIN classic_exam_questions q ON q.part_id=p.id WHERE p.exam_id IN ({markers})
            GROUP BY p.id ORDER BY p.exam_id,p.sort_order,p.id""",
        exam_ids,
    ):
        bundles[row["exam_id"]]["parts"].append(row)
    for row in db.all(
        f"SELECT exam_id,grade,min_score FROM classic_exam_grade_thresholds WHERE exam_id IN ({markers}) ORDER BY exam_id,grade",
        exam_ids,
    ):
        bundles[row["exam_id"]]["thresholds"].append(
            {"grade": row["grade"], "minScore": row["min_score"]}
        )
    return bundles


def readiness(
    db,
    exam_id,
    assignment_id=None,
    include_window=False,
    bundle=None,
    assignment_data=None,
):
    bundle = bundle or readiness_bundles(db, [exam_id])[exam_id]
    config = bundle.get("config")
    if not config:
        fail("settings_missing", status=500)
    parts = bundle["parts"]
    errors = []

    def add(
        code,
        message,
        scope="configuration",
        section="basic",
        entity_id=None,
        field=None,
        **details,
    ):
        errors.append(
            {
                "code": code,
                "message": message,
                "scope": scope,
                "section": section,
                "entityId": entity_id,
                "field": field,
                "blocking": True,
                "details": details,
            }
        )

    for error in config_errors(config):
        add(error["code"], error["message"], field=error["field"])
    if not parts:
        add("parts_required", "Добавьте хотя бы одну часть", "part", "questions")
    for error in validate_thresholds(calculate_max_score(parts), bundle["thresholds"]):
        add(error["code"], error["message"], "scoring", "scoring", field="thresholds")
    reserve = 0
    if assignment_id:
        assignment_data = assignment_data or get_assignment(db, exam_id, assignment_id)
        members = assignment_data["members"]
        reserve = assignment_data["replacementLimit"]
        if not 1 <= len(members) <= 6:
            add(
                "invalid_commission_size",
                "Нужна комиссия от 1 до 6 человек",
                "assignment",
                "commissions",
            )
        for member in members:
            valid = (
                member["id"] in bundle["validExaminatorIds"]
                if "validExaminatorIds" in bundle
                else bool(
                    db.one(
                        "SELECT id FROM auth_users WHERE role='examinator' AND ref_id=%s",
                        (member["id"],),
                    )
                )
            )
            if not valid:
                add(
                    "examinator_not_found",
                    "У экзаменатора нет аккаунта",
                    "assignment",
                    "commissions",
                    member["id"],
                )
        if not assignment_data["student"]["login"]:
            add(
                "student_not_found",
                "У студента нет аккаунта",
                "assignment",
                "assignments",
                assignment_data["student"]["id"],
            )
    for part in parts:
        if not part["question_weight"] or not part["question_count"]:
            add(
                "part_settings_required",
                f"Укажите вес и число вопросов части {part['code']}",
                "part",
                "questions",
                part["id"],
            )
        required = (part["question_count"] or 0) + reserve
        if part["bank_size"] < required:
            add(
                "part_bank_too_small",
                f"В части {part['code']} нужно не менее {required} вопросов",
                "part",
                "questions",
                part["id"],
                required=required,
                available=part["bank_size"],
                partCode=part["code"],
            )
    if config["fractional_mode"] == "extra_question":
        source = config["tie_breaker_source_mode"]
        if source == "specific_part":
            chosen = next(
                (p for p in parts if p["id"] == config["tie_breaker_part_id"]), None
            )
            size = chosen["bank_size"] if chosen else 0
        else:
            size = sum(p["bank_size"] for p in parts)
        if source and size < reserve + 2:
            add(
                "tie_breaker_bank_too_small",
                f"Для дополнительных вопросов нужно не менее {reserve+2} вопросов",
                "part",
                "questions",
                required=reserve + 2,
                available=size,
            )
    configured = (
        readiness(db, exam_id, bundle=bundle)["isConfigured"]
        if assignment_id
        else not errors
    )
    assignment_ready = not errors
    if include_window:
        current = now()
        if (
            not config["start_at"]
            or not config["end_at"]
            or not config["start_at"] <= current <= config["end_at"]
        ):
            add(
                "exam_outside_window",
                "Сейчас вне периода начала сдачи",
                "window",
                "basic",
            )
    can_start = not errors
    if assignment_id:
        can_start = can_start and assignment_data["status"] == "pending_ready"
        if can_start:
            if "allMembersReady" in assignment_data:
                can_start = assignment_data["allMembersReady"]
            else:
                members = db.all(
                    "SELECT ready_at FROM classic_exam_attempt_members WHERE attempt_id=%s",
                    (assignment_data["attemptId"],),
                )
                can_start = bool(members) and all(m["ready_at"] for m in members)
    return {
        "isConfigured": configured,
        "configurationReady": configured,
        "assignmentReady": assignment_ready,
        "canPrepare": assignment_ready,
        "canStartNow": bool(can_start),
        "errors": errors,
        "warnings": [],
        "checkedAt": iso(now()),
    }


def outside_result(db, exam_id, result_id):
    row = db.one(
        "SELECT r.*,s.full_name student_name FROM exam_sessions r JOIN students s ON s.id=r.student_id WHERE r.exam_id=%s AND r.id=%s",
        (exam_id, result_id),
    )
    if not row:
        fail("result_not_found", status=404)
    return {
        "id": row["id"],
        "examId": row["exam_id"],
        "studentId": row["student_id"],
        "studentName": row["student_name"],
        "points": row["val"],
        "grade": row["points"],
        "examinator": row["examinator"],
        "version": row["version"],
        "createdAt": iso(row["created_at"]),
        "updatedAt": iso(row["updated_at"]),
    }


def save_outside_result(db, exam_id, payload, result_id=None):
    allowed = (
        ("points", "grade", "examinator", "expectedVersion")
        if result_id
        else ("studentId", "points", "grade", "examinator")
    )
    fields(payload, allowed, ("expectedVersion",) if result_id else allowed)
    row = (
        require_row(db, "exam_sessions", result_id, exam_id, "result_not_found")
        if result_id
        else None
    )
    if row:
        check_version(row, payload["expectedVersion"], "result_modified")
    data = {}
    if "points" in payload:
        data["val"] = decimal_number(payload["points"])
    if "grade" in payload:
        data["points"] = integer(payload["grade"], "grade", 0, 5)
    if "examinator" in payload:
        data["examinator"] = text_value(payload["examinator"], "examinator", 255)
    if row:
        changes = {k: v for k, v in data.items() if row[k] != v}
        if changes:
            db.update(
                "exam_sessions",
                dict(changes, version=row["version"] + 1),
                "id=%s",
                (result_id,),
            )
            invalidate_rating(db)
    else:
        student_id = integer(payload["studentId"], "student_id")
        account(db, "student", student_id, False)
        existing = db.one(
            "SELECT id FROM exam_sessions WHERE exam_id=%s AND student_id=%s",
            (exam_id, student_id),
        )
        if existing:
            fail(
                "outside_result_already_exists",
                "У студента уже есть результат",
                409,
                existingResultId=existing["id"],
            )
        result_id = db.insert(
            "exam_sessions", dict(data, exam_id=exam_id, student_id=student_id)
        )
        invalidate_rating(db)
    return outside_result(db, exam_id, result_id)


def list_outside_results(db, exam_id, args):
    page, limit, offset = page_args(args)
    where, params = ["r.exam_id=%s"], [exam_id]
    if args.get("search"):
        where.append(
            "(s.full_name LIKE %s ESCAPE '!' OR r.examinator LIKE %s ESCAPE '!' OR CAST(s.id AS CHAR)=%s)"
        )
        params.extend([search_value(args), search_value(args), args["search"]])
    order = {
        "student_asc": "s.full_name,r.id",
        "grade_desc": "r.points DESC,r.id",
        "points_desc": "r.val DESC,r.id",
        "updated_desc": "r.updated_at DESC,r.id",
    }
    sort = enum_value(args.get("sort", "student_asc"), order, "sort")
    joins = (
        "FROM exam_sessions r JOIN students s ON s.id=r.student_id WHERE "
        + " AND ".join(where)
    )
    total = db.scalar("SELECT COUNT(*) " + joins, params)
    ids = db.all(
        "SELECT r.id " + joins + " ORDER BY " + order[sort] + " LIMIT %s OFFSET %s",
        params + [limit, offset],
    )
    return {
        "items": [outside_result(db, exam_id, r["id"]) for r in ids],
        "pagination": pagination(total, page, limit),
    }
