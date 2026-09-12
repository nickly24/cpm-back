"""Exam-only rating snapshots and one published-detail read model."""

from datetime import datetime, time, timedelta, timezone
from flask import current_app, has_app_context

from .common import MOSCOW, UTC, UnitOfWork, transaction, parse_date, now, iso, loads


def enabled():
    if has_app_context():
        return bool(current_app.config.get("RATING_EXAMS_V2_ENABLED", False))
    from cpm_back.config import config

    return bool(config.RATING_EXAMS_V2_ENABLED)


def period_bounds(date_from, date_to):
    start = parse_date(date_from) if isinstance(date_from, str) else date_from
    end = parse_date(date_to) if isinstance(date_to, str) else date_to
    if start > end:
        raise ValueError("Начало периода должно быть не позже окончания")
    return (
        datetime.combine(start, time.min, MOSCOW).astimezone(UTC).replace(tzinfo=None),
        datetime.combine(end + timedelta(days=1), time.min, MOSCOW)
        .astimezone(UTC)
        .replace(tzinfo=None),
    )


def capture_exam_input(db, date_from, date_to, as_of=None):
    """Caller must own one RR read transaction; release before long calculation."""
    as_of = as_of or now()
    if as_of.tzinfo:
        as_of = as_of.astimezone(UTC).replace(tzinfo=None)
    start, end = period_bounds(date_from, date_to)
    revision = db.scalar("SELECT source_revision FROM rating_source_state WHERE id=1")
    exams = db.all(
        """SELECT e.id,e.exam_type,e.direction_id,COALESCE(d.name,e.name) exam_name,
             e.date,s.start_at FROM exams e LEFT JOIN directions d ON d.id=e.direction_id
             LEFT JOIN classic_exam_settings s ON s.exam_id=e.id
             WHERE (e.exam_type='outside_lms' AND e.date BETWEEN %s AND %s)
                OR (e.exam_type='classic' AND s.start_at >= %s AND s.start_at < %s AND s.start_at <= %s)
             ORDER BY e.id""",
        (date_from, date_to, start, end, as_of),
    )
    future = db.scalar(
        """SELECT MIN(s.start_at) FROM classic_exam_settings s
        JOIN exams e ON e.id=s.exam_id WHERE e.exam_type='classic' AND s.start_at>=%s
        AND s.start_at<%s AND s.start_at>%s""",
        (start, end, as_of),
        None,
    )
    values = {}
    if exams:
        ids = [e["id"] for e in exams]
        placeholders = ",".join(["%s"] * len(ids))
        for row in db.all(
            f"""SELECT r.exam_id,r.student_id,r.points FROM exam_sessions r
            JOIN exams e ON e.id=r.exam_id WHERE e.exam_type='outside_lms' AND e.id IN ({placeholders})""",
            ids,
        ):
            values[(row["exam_id"], row["student_id"])] = {
                "grade": float(row["points"]),
                "attempt_no": None,
                "has_appeal": False,
            }
        from .results import RESULT_SELECT, CURRENT_CLAUSE

        for row in db.all(
            RESULT_SELECT
            + f" WHERE {CURRENT_CLAUSE} AND a.exam_id IN ({placeholders})",
            ids,
        ):
            values[(row["exam_id"], row["student_id"])] = {
                "grade": int(row["effective_grade"]),
                "attempt_no": row["attempt_no"],
                "has_appeal": row["latest_appeal_id"] is not None,
            }
    return {
        "sourceRevision": revision,
        "asOf": as_of,
        "nextExamStartAt": future,
        "exams": exams,
        "values": values,
        "students": [r["id"] for r in db.all("SELECT id FROM students ORDER BY id")],
    }


def calculate_from_snapshot(snapshot, student_id):
    details = []
    for exam in snapshot["exams"]:
        value = snapshot["values"].get((exam["id"], student_id))
        exam_date = (
            exam["start_at"].replace(tzinfo=UTC).astimezone(MOSCOW).date()
            if exam["exam_type"] == "classic"
            else exam["date"]
        )
        details.append(
            {
                "exam_id": exam["id"],
                "exam_name": exam["exam_name"],
                "exam_date": str(exam_date),
                "score": value["grade"] if value else 0,
                "status": "Сдан" if value else "Не сдавал",
                "exam_type": exam["exam_type"],
                "direction_id": exam["direction_id"],
                "attempt_no": value["attempt_no"] if value else None,
                "has_appeal": bool(value and value["has_appeal"]),
                "missing_result": value is None,
            }
        )
    total = sum(item["score"] for item in details)
    return {
        "average": total / len(details) if details else 0,
        "total_count": len(details),
        "total_score": total,
        "details": details,
    }


def freshness(db, at=None):
    row = db.one("SELECT * FROM rating_source_state WHERE id=1")
    if not row:
        return {
            "scope": "exams",
            "isStale": True,
            "reason": "never_calculated",
            "activeJobId": None,
        }
    reason = None
    if row["calculated_revision"] is None:
        reason = "never_calculated"
    elif row["source_revision"] != row["calculated_revision"]:
        reason = "exam_data_changed"
    elif row["next_exam_start_at"] is not None and row["next_exam_start_at"] <= (
        at or now()
    ):
        reason = "exam_started"
    last = db.one(
        "SELECT status FROM rating_recalc_jobs WHERE exam_source_revision IS NOT NULL ORDER BY id DESC LIMIT 1"
    )
    if last and last["status"] == "failed" and reason:
        reason = "recalculation_failed"
    return {
        "scope": "exams",
        "isStale": bool(reason),
        "reason": reason,
        "sourceRevision": row["source_revision"],
        "calculatedRevision": row["calculated_revision"],
        "dateFrom": iso(row["date_from"]),
        "dateTo": iso(row["date_to"]),
        "calculatedAt": iso(row["calculated_at"]),
        "asOf": iso(row["as_of"]),
        "activeJobId": row["active_job_id"],
    }


def freshness_response(connection=None):
    if not enabled():
        return {}
    if connection is not None:
        db = UnitOfWork(connection)
        try:
            return {"ratingFreshness": freshness(db)}
        finally:
            db.cursor.close()
    with transaction(write=False) as db:
        return {"ratingFreshness": freshness(db)}


def published_details(db, rating_ids=None, student_id=None, mongo_db=None):
    """Only rows published in Allratings select a detail document, including fallback."""
    where, params = [], []
    if rating_ids is not None:
        if not rating_ids:
            return {}
        where.append("id IN (" + ",".join(["%s"] * len(rating_ids)) + ")")
        params.extend(rating_ids)
    if student_id is not None:
        where.append("student_id=%s")
        params.append(student_id)
    rows = db.all(
        "SELECT id,student_id,details_json FROM Allratings"
        + (" WHERE " + " AND ".join(where) if where else ""),
        params,
    )
    result = {
        r["id"]: loads(r["details_json"]) for r in rows if r["details_json"] is not None
    }
    legacy_ids = [r["id"] for r in rows if r["details_json"] is None]
    if legacy_ids:
        if mongo_db is None:
            from cpm_back.db.mongo import get_mongo_db

            mongo_db = get_mongo_db()
        for document in mongo_db.rate_rec.find({"rating_id": {"$in": legacy_ids}}):
            document.pop("_id", None)
            result[document["rating_id"]] = document
    names = {
        r["id"]: r["name"]
        for r in db.all(
            "SELECT e.id,COALESCE(d.name,e.name) name FROM exams e LEFT JOIN directions d ON d.id=e.direction_id"
        )
    }
    for row in rows:
        document = result.get(row["id"])
        if document is None:
            continue
        document["rating_id"] = row["id"]
        document["student_id"] = row["student_id"]
        for exam in (document.get("exams") or {}).get("details") or []:
            if exam.get("exam_id") in names:
                exam["exam_name"] = names[exam["exam_id"]]
    return result


def read_published_details(connection=None, **filters):
    if connection is not None:
        db = UnitOfWork(connection)
        try:
            return published_details(db, **filters)
        finally:
            db.cursor.close()
    with transaction(write=False) as db:
        return published_details(db, **filters)


def read_published_bundle(connection=None, **filters):
    """Read published values and their freshness from the same RR snapshot.

    Callers supplying a connection must retain its read transaction for the
    whole response, just like the rating list/report adapters.
    """
    if connection is not None:
        db = UnitOfWork(connection)
        try:
            return published_details(db, **filters), {"ratingFreshness": freshness(db)}
        finally:
            db.cursor.close()
    with transaction(write=False) as db:
        return published_details(db, **filters), {"ratingFreshness": freshness(db)}
