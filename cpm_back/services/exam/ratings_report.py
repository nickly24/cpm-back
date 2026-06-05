"""
Развёрнутый отчёт по рейтингу: студенты × колонки (ДЗ / экзамены / тесты) + сводные баллы.
"""
from __future__ import annotations

from cpm_back.db.mongo import get_mongo_db
from cpm_back.db.mysql_pool import close_db_connection, get_db_connection
from cpm_back.services.serv.school_schema import is_schools_schema_ready


def _column(kind: str, key: str, label: str, subtitle: str | None = None) -> dict:
    return {
        "key": key,
        "kind": kind,
        "label": label,
        "subtitle": subtitle,
    }


def _parse_sort_date(value) -> str:
    if not value:
        return "9999-99-99"
    return str(value).strip()[:10]


def get_ratings_report():
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if is_schools_schema_ready(cursor):
            cursor.execute(
                """
                SELECT
                    ar.id AS rating_id,
                    ar.student_id,
                    ar.homework,
                    ar.exams,
                    ar.tests,
                    ar.final,
                    s.full_name,
                    s.class,
                    s.group_id,
                    g.name AS group_name,
                    s.school_id,
                    sch.short_name AS school_short_name
                FROM Allratings ar
                JOIN students s ON s.id = ar.student_id
                LEFT JOIN `groups` g ON g.id = s.group_id
                LEFT JOIN schools sch ON sch.id = s.school_id
                ORDER BY ar.final DESC, s.full_name ASC
                """
            )
        else:
            cursor.execute(
                """
                SELECT
                    ar.id AS rating_id,
                    ar.student_id,
                    ar.homework,
                    ar.exams,
                    ar.tests,
                    ar.final,
                    s.full_name,
                    s.class,
                    s.group_id,
                    g.name AS group_name
                FROM Allratings ar
                JOIN students s ON s.id = ar.student_id
                LEFT JOIN `groups` g ON g.id = s.group_id
                ORDER BY ar.final DESC, s.full_name ASC
                """
            )

        rows = cursor.fetchall()
        cursor.close()

        if not rows:
            return {
                "status": True,
                "period": None,
                "students": [],
                "columns": [],
                "values": [],
                "message": "Рейтинг ещё не рассчитан",
            }

        mongo_db = get_mongo_db()
        rating_ids = [row["rating_id"] for row in rows]
        details_by_rating: dict[int, dict] = {}
        for doc in mongo_db.rate_rec.find({"rating_id": {"$in": rating_ids}}):
            details_by_rating[int(doc["rating_id"])] = doc

        period = None
        homework_meta: dict[str, dict] = {}
        exam_meta: dict[str, dict] = {}
        test_meta: dict[str, dict] = {}

        for doc in details_by_rating.values():
            if period is None and doc.get("date_from") and doc.get("date_to"):
                period = {
                    "date_from": str(doc["date_from"])[:10],
                    "date_to": str(doc["date_to"])[:10],
                    "calculated_at": doc.get("calculated_at"),
                }

            for item in (doc.get("homework") or {}).get("details") or []:
                key = f"hw_{item.get('homework_id')}"
                homework_meta[key] = {
                    "label": item.get("name") or f"ДЗ #{item.get('homework_id')}",
                    "subtitle": _parse_sort_date(item.get("deadline")),
                    "sort": _parse_sort_date(item.get("deadline")),
                }

            for item in (doc.get("exams") or {}).get("details") or []:
                key = f"ex_{item.get('exam_id')}"
                exam_meta[key] = {
                    "label": item.get("exam_name") or f"Экзамен #{item.get('exam_id')}",
                    "subtitle": _parse_sort_date(item.get("exam_date")),
                    "sort": _parse_sort_date(item.get("exam_date")),
                }

            for item in (doc.get("tests") or {}).get("details") or []:
                test_id = item.get("test_id")
                if not test_id:
                    continue
                key = f"ts_{test_id}"
                direction = (item.get("direction") or "").strip()
                title = item.get("title") or "Тест"
                test_meta[key] = {
                    "label": title,
                    "subtitle": direction or None,
                    "sort": f"{direction.casefold()}|{title.casefold()}",
                }

        columns: list[dict] = [
            _column("summary", "sum_homework", "ДЗ (ср.)"),
            _column("summary", "sum_exams", "Экз. (ср.)"),
            _column("summary", "sum_tests", "Тесты (ср.)"),
            _column("summary", "sum_final", "Итог"),
        ]

        for key, meta in sorted(homework_meta.items(), key=lambda x: (x[1]["sort"], x[1]["label"])):
            columns.append(_column("homework", key, meta["label"], meta.get("subtitle")))

        for key, meta in sorted(exam_meta.items(), key=lambda x: (x[1]["sort"], x[1]["label"])):
            columns.append(_column("exam", key, meta["label"], meta.get("subtitle")))

        for key, meta in sorted(test_meta.items(), key=lambda x: (x[1]["sort"], x[1]["label"])):
            columns.append(_column("test", key, meta["label"], meta.get("subtitle")))

        students = []
        values = []

        for row in rows:
            rating_id = row["rating_id"]
            student_id = row["student_id"]
            student = {
                "student_id": student_id,
                "rating_id": rating_id,
                "full_name": row["full_name"],
                "class": row.get("class"),
                "group_id": row.get("group_id"),
                "group_name": row.get("group_name"),
                "school_id": row.get("school_id"),
                "school_short_name": row.get("school_short_name"),
                "homework": float(row["homework"] or 0),
                "exams": float(row["exams"] or 0),
                "tests": float(row["tests"] or 0),
                "final": float(row["final"] or 0),
            }
            students.append(student)

            def append_value(column_key: str, score: float, status: str | None = None):
                values.append(
                    {
                        "student_id": student_id,
                        "column_key": column_key,
                        "score": round(float(score), 2),
                        "status": status,
                    }
                )

            append_value("sum_homework", student["homework"])
            append_value("sum_exams", student["exams"])
            append_value("sum_tests", student["tests"])
            append_value("sum_final", student["final"])

            doc = details_by_rating.get(rating_id)
            if not doc:
                continue

            for item in (doc.get("homework") or {}).get("details") or []:
                append_value(
                    f"hw_{item.get('homework_id')}",
                    float(item.get("score") or 0),
                    item.get("status"),
                )

            for item in (doc.get("exams") or {}).get("details") or []:
                append_value(
                    f"ex_{item.get('exam_id')}",
                    float(item.get("score") or 0),
                    item.get("status"),
                )

            for item in (doc.get("tests") or {}).get("details") or []:
                test_id = item.get("test_id")
                if not test_id:
                    continue
                append_value(
                    f"ts_{test_id}",
                    float(item.get("score") or 0),
                    item.get("source"),
                )

        return {
            "status": True,
            "period": period,
            "students": students,
            "columns": columns,
            "values": values,
        }

    except Exception as err:
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
