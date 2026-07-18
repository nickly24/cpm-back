"""
Расчёт рейтинга студентов: ДЗ, экзамены, тесты (MySQL + MongoDB).
Использует переданные mysql_conn и mongo_db (пул/клиент приложения).
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def _as_moscow(value: datetime) -> datetime:
    """Treat legacy naive values as Moscow time and normalize aware values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=MOSCOW_TZ)
    return value.astimezone(MOSCOW_TZ)


def _coerce_rating_date(value) -> datetime:
    if isinstance(value, datetime):
        return _as_moscow(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=MOSCOW_TZ)
    return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").replace(tzinfo=MOSCOW_TZ)


def _parse_test_start_date(raw) -> datetime | None:
    if not raw:
        return None
    try:
        text = str(raw).strip()
        if "T" in text:
            return _as_moscow(datetime.fromisoformat(text.replace("Z", "+00:00")))
        return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=MOSCOW_TZ)
    except (TypeError, ValueError):
        return None


def _test_start_in_period(test, date_from_dt: datetime, date_to_dt: datetime) -> bool:
    start = _parse_test_start_date(test.get("startDate"))
    if not start:
        return False
    # Rating inputs are calendar dates, so the end date must include the whole day.
    return date_from_dt.date() <= start.date() <= date_to_dt.date()


def _is_mongo_test_eligible_for_rating(test, date_from_dt: datetime, date_to_dt: datetime) -> bool:
    """Только опубликованные и активные тесты (как в каталоге для студентов)."""
    if test.get("published") is False:
        return False
    if test.get("isActive") is False:
        return False
    return _test_start_in_period(test, date_from_dt, date_to_dt)


def _normalize_direction_label(name: str | None) -> str:
    return (name or "Неизвестное направление").strip() or "Неизвестное направление"


def _direction_group_key(name: str | None) -> str:
    return _normalize_direction_label(name).casefold()


def _average_direction_ratings(direction_averages: dict[str, float]) -> float:
    """Average only directions that have tests and therefore have a bucket."""
    if not direction_averages:
        return 0.0
    return sum(direction_averages.values()) / len(direction_averages)


def calculate_homework_rating(mysql_conn, student_id, date_from, date_to):
    cursor = mysql_conn.cursor(dictionary=True)
    query = """
        SELECT h.id, h.name, h.type, h.deadline, hs.result, hs.status, hs.date_pass
        FROM homework h
        LEFT JOIN homework_sessions hs ON h.id = hs.homework_id AND hs.student_id = %s
        WHERE h.type = 'ОВ' AND h.deadline >= %s AND h.deadline <= %s AND h.deadline < CURDATE()
        ORDER BY h.deadline DESC
    """
    cursor.execute(query, (student_id, date_from, date_to))
    homeworks = cursor.fetchall()
    cursor.close()
    total_score = 0
    completed_count = 0
    details = []
    for hw in homeworks:
        status, score = "Не сдано", 0
        if hw['status'] == 1 and hw['result'] is not None:
            status, score = "Сдано", float(hw['result'])
            completed_count += 1
        total_score += score
        details.append({
            'homework_id': hw['id'], 'name': hw['name'],
            'deadline': str(hw['deadline']) if hw['deadline'] else None,
            'score': score, 'status': status,
            'date_pass': str(hw['date_pass']) if hw['date_pass'] else None
        })
    average_score = total_score / len(homeworks) if homeworks else 0
    return {
        'average': average_score, 'total_count': len(homeworks),
        'completed_count': completed_count, 'total_score': total_score, 'details': details
    }


def calculate_exams_rating(mysql_conn, student_id, date_from, date_to):
    cursor = mysql_conn.cursor(dictionary=True)
    query = """
        SELECT es.id, es.points as score, e.id as exam_id, e.name as exam_name, e.date as exam_date
        FROM exams e
        LEFT JOIN exam_sessions es ON es.exam_id = e.id AND es.student_id = %s
        WHERE e.date >= %s AND e.date <= %s
        ORDER BY e.date DESC
    """
    cursor.execute(query, (student_id, date_from, date_to))
    exams = cursor.fetchall()
    cursor.close()
    total_score = 0
    details = []
    for exam in exams:
        score = float(exam['score']) if exam['score'] is not None else 0
        total_score += score
        details.append({
            'exam_id': exam['exam_id'], 'exam_name': exam['exam_name'],
            'exam_date': str(exam['exam_date']) if exam['exam_date'] else None,
            'score': score, 'status': 'Сдан' if exam['score'] is not None else 'Не сдавал'
        })
    average_score = total_score / len(exams) if exams else 0
    return {'average': average_score, 'total_count': len(exams), 'total_score': total_score, 'details': details}


def calculate_tests_rating(mysql_conn, mongo_db, student_id, date_from, date_to):
    tests_collection = mongo_db.tests
    test_sessions_collection = mongo_db.test_sessions
    cursor = mysql_conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name FROM directions")
    directions = {d['id']: d['name'] for d in cursor.fetchall()}
    cursor.close()

    student_sessions: dict[str, float] = {}
    sid_int = int(student_id) if student_id is not None else None
    sid_str = str(student_id)
    for session in test_sessions_collection.find(
        {"studentId": {"$in": [sid_int, sid_str]}},
        {"testId": 1, "score": 1},
    ):
        score = session.get("score", 0)
        student_sessions[str(session["testId"])] = float(score) if score is not None else 0.0

    cursor = mysql_conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT t.id, t.name, t.direction_id, t.date, ts.rate
        FROM tests_out t
        LEFT JOIN test_sessions ts ON t.id = ts.test_id AND ts.student_id = %s
        WHERE t.date >= %s AND t.date <= %s
        ORDER BY t.date DESC
    """, (student_id, date_from, date_to))
    external_tests = cursor.fetchall()
    cursor.close()

    date_from_dt = _coerce_rating_date(date_from)
    date_to_dt = _coerce_rating_date(date_to)

    mongo_tests_by_id = {str(test["_id"]): test for test in tests_collection.find({})}

    # direction_key -> {label, tests[], scores[]}
    directions_dict: dict[str, dict] = {}
    total_tests_count = 0

    def _append_test(direction_name, test_id, test_title, score, source):
        nonlocal total_tests_count
        key = _direction_group_key(direction_name)
        label = _normalize_direction_label(direction_name)
        bucket = directions_dict.setdefault(
            key,
            {"label": label, "tests": [], "scores": []},
        )
        if not bucket["label"]:
            bucket["label"] = label
        bucket["tests"].append(
            {
                "test_id": test_id,
                "title": test_title,
                "score": score,
                "source": source,
            },
        )
        bucket["scores"].append(score)
        total_tests_count += 1

    # MongoDB: все опубликованные/активные тесты со startDate в периоде.
    # Нет test_session → 0 (пропуск теста учитывается в среднем).
    for test in mongo_tests_by_id.values():
        if not _is_mongo_test_eligible_for_rating(test, date_from_dt, date_to_dt):
            continue
        test_id = str(test["_id"])
        score = student_sessions.get(test_id, 0.0)
        _append_test(
            test.get("direction"),
            test_id,
            test.get("title", "Без названия"),
            score,
            "MongoDB",
        )

    for test in external_tests:
        direction_id = test.get("direction_id")
        direction_name = directions.get(direction_id, f"Направление ID {direction_id}")
        score = float(test.get("rate", 0)) if test.get("rate") is not None else 0.0
        _append_test(
            direction_name,
            f"external_{test['id']}",
            test.get("name", "Без названия"),
            score,
            "MySQL (внешний)",
        )

    direction_averages: dict[str, float] = {}
    all_tests_details: list[dict] = []
    for bucket in directions_dict.values():
        label = bucket["label"]
        scores = bucket["scores"]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        direction_averages[label] = avg_score
        for test_info in bucket["tests"]:
            all_tests_details.append(
                {
                    "direction": label,
                    "test_id": test_info["test_id"],
                    "title": test_info["title"],
                    "score": test_info["score"],
                    "source": test_info["source"],
                },
            )

    # Each direction has equal weight. Directions without tests in the period
    # never create a bucket and therefore do not participate in the average.
    overall_average = _average_direction_ratings(direction_averages)
    return {
        'average': overall_average,
        'total_count': total_tests_count,
        'directions': direction_averages,
        'details': all_tests_details,
    }


def calculate_final_rating(hw_rating, exams_rating, tests_rating):
    hw_component = (hw_rating['average'] * 25) / 100
    exams_component = exams_rating['average'] * 6
    tests_component = (tests_rating['average'] * 45) / 100
    return hw_component + exams_component + tests_component


def calculate_student_rating(mysql_conn, mongo_db, student_id, date_from, date_to):
    hw_rating = calculate_homework_rating(mysql_conn, student_id, date_from, date_to)
    exams_rating = calculate_exams_rating(mysql_conn, student_id, date_from, date_to)
    tests_rating = calculate_tests_rating(mysql_conn, mongo_db, student_id, date_from, date_to)
    final_rating = calculate_final_rating(hw_rating, exams_rating, tests_rating)
    return {
        'student_id': student_id, 'date_from': date_from, 'date_to': date_to,
        'homework': {'rating': hw_rating['average'], 'details': hw_rating['details']},
        'exams': {'rating': exams_rating['average'], 'details': exams_rating['details']},
        'tests': {'rating': tests_rating['average'], 'details': tests_rating['details'], 'directions': tests_rating['directions']},
        'final_rating': final_rating
    }
