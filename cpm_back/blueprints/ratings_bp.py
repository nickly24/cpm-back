"""
Рейтинги: список (MySQL Allratings), детализация (MongoDB rate_rec), пересчёт всех рейтингов.
"""
from datetime import datetime
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_auth, require_role, require_self_or_role
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from cpm_back.db.mongo import get_mongo_db
from cpm_back.services.exam.save_ratings import save_all_ratings

ratings_bp = Blueprint('ratings', __name__, url_prefix='')


def _fetch_ratings_rows(mysql_conn):
    cursor = mysql_conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT ar.id, ar.student_id, ar.exams, ar.homework, ar.tests, ar.final,
               s.full_name as student_name, s.class as student_class, g.name as group_name
        FROM Allratings ar
        LEFT JOIN students s ON ar.student_id = s.id
        LEFT JOIN `groups` g ON s.group_id = g.id
        ORDER BY ar.final DESC, s.full_name ASC
    """)
    return cursor.fetchall()


def _format_ratings(rows):
    formatted = []
    for r in rows:
        formatted.append({
            'id': r['id'], 'student_id': r['student_id'],
            'student_name': r.get('student_name', 'Неизвестно'),
            'student_class': r.get('student_class'), 'group_name': r.get('group_name'),
            'exams': float(r['exams']) if r['exams'] is not None else 0,
            'homework': float(r['homework']) if r['homework'] is not None else 0,
            'tests': float(r['tests']) if r['tests'] is not None else 0,
            'final': float(r['final']) if r['final'] is not None else 0
        })
    return formatted


@ratings_bp.route('/get-all-ratings', methods=['GET'])
@require_role('admin', 'supervisor')
def all_ratings(current_user=None):
    mysql_conn = None
    try:
        mysql_conn = get_db_connection()
        ratings = _fetch_ratings_rows(mysql_conn)
        formatted = _format_ratings(ratings)
        return jsonify({"status": True, "ratings": formatted, "total": len(formatted)})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500
    finally:
        if mysql_conn:
            close_db_connection(mysql_conn)


@ratings_bp.route('/get-all-rating', methods=['GET'])
@require_role('admin', 'supervisor')
def all_ratings_legacy(current_user=None):
    """
    Legacy-роут для старого фронта SupervisorFunctions/StudentsPanel.js
    Возвращает data.students в прежнем формате.
    """
    mysql_conn = None
    try:
        mysql_conn = get_db_connection()
        rows = _fetch_ratings_rows(mysql_conn)
        students = []
        for r in rows:
            students.append({
                "id": r["student_id"],
                "full_name": r.get("student_name") or "Неизвестно",
                "homework_rate": float(r["homework"]) if r["homework"] is not None else 0,
                "test_rate": float(r["tests"]) if r["tests"] is not None else 0,
                "exam_rate": float(r["exams"]) if r["exams"] is not None else 0,
                "rate": float(r["final"]) if r["final"] is not None else 0
            })
        return jsonify({"status": True, "data": {"students": students}})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500
    finally:
        if mysql_conn:
            close_db_connection(mysql_conn)


@ratings_bp.route('/get-rating-details', methods=['POST'])
@require_role('admin', 'supervisor')
def rating_details(current_user=None):
    data = request.get_json()
    if not data:
        return jsonify({"status": False, "error": "Данные не предоставлены"}), 400
    rating_id = data.get('rating_id')
    if not rating_id:
        return jsonify({"status": False, "error": "rating_id обязателен"}), 400
    try:
        rating_id = int(rating_id)
    except (ValueError, TypeError):
        return jsonify({"status": False, "error": "rating_id должен быть числом"}), 400
    try:
        mongo_db = get_mongo_db()
        details = mongo_db.rate_rec.find_one({'rating_id': rating_id})
        if not details:
            return jsonify({"status": False, "error": f"Детализация для rating_id {rating_id} не найдена"}), 404
        if '_id' in details:
            details['_id'] = str(details['_id'])
        return jsonify({"status": True, "details": details})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500


@ratings_bp.route('/calculate-all-ratings', methods=['POST'])
@require_role('admin')
def calculate_all(current_user=None):
    data = request.get_json()
    if not data:
        return jsonify({"status": False, "error": "Данные не предоставлены"}), 400
    date_from = data.get('date_from')
    date_to = data.get('date_to')
    if not date_from or not date_to:
        return jsonify({"status": False, "error": "date_from и date_to обязательны"}), 400
    try:
        datetime.strptime(date_from, "%Y-%m-%d")
        datetime.strptime(date_to, "%Y-%m-%d")
    except ValueError:
        return jsonify({"status": False, "error": "Неверный формат даты. Ожидается YYYY-MM-DD"}), 400
    mysql_conn = None
    try:
        mysql_conn = get_db_connection()
        mongo_db = get_mongo_db()
        results = save_all_ratings(mysql_conn, mongo_db, date_from, date_to)
        message_parts = [f"Обработано студентов: {results['successful']}/{results['total_students']}"]
        if results.get('skipped', 0) > 0:
            message_parts.append(f"Пропущено: {results['skipped']}")
        if results.get('failed', 0) > 0:
            message_parts.append(f"Ошибок: {results['failed']}")
        return jsonify({"status": True, "message": " | ".join(message_parts), "results": results})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500
    finally:
        if mysql_conn:
            close_db_connection(mysql_conn)


@ratings_bp.route('/my-rating', methods=['GET'])
@require_auth
def my_rating(current_user=None):
    """
    Рейтинг текущего студента по JWT (только свой). Для дашборда успеваемости.
    Возвращает документ из MongoDB rate_rec: homework.rating, exams.rating, tests.rating.
    """
    role = (current_user or {}).get('role')
    student_id = (current_user or {}).get('id')
    if role != 'student' or student_id is None:
        return jsonify({"status": False, "error": "Доступно только для студента"}), 403
    try:
        # В БД student_id может быть int
        sid = int(student_id) if isinstance(student_id, str) and student_id.isdigit() else student_id
        mongo_db = get_mongo_db()
        cursor = mongo_db.rate_rec.find(
            {'student_id': sid},
            {'_id': 0, 'rating_id': 1, 'student_id': 1, 'date_from': 1, 'date_to': 1,
             'calculated_at': 1, 'homework.rating': 1, 'exams.rating': 1, 'tests.rating': 1}
        ).sort('calculated_at', -1).limit(1)
        doc = next(cursor, None)
        if not doc:
            return jsonify({"status": True, "data": None, "message": "Рейтинг ещё не рассчитан"})
        # Преобразуем для фронта: округлённые баллы по трём направлениям
        hw = doc.get('homework') or {}
        ex = doc.get('exams') or {}
        ts = doc.get('tests') or {}
        out = {
            'rating_id': doc.get('rating_id'),
            'student_id': doc.get('student_id'),
            'date_from': doc.get('date_from'),
            'date_to': doc.get('date_to'),
            'calculated_at': doc.get('calculated_at'),
            'homework': {'rating': round(float(hw.get('rating', 0)), 2)},
            'exams': {'rating': round(float(ex.get('rating', 0)), 2)},
            'tests': {'rating': round(float(ts.get('rating', 0)), 2)},
        }
        return jsonify({"status": True, "data": out})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500


@ratings_bp.route('/student-rating', methods=['POST'])
@require_self_or_role('student_id', 'admin', 'supervisor', 'proctor')
def student_rating(current_user=None):
    """
    Legacy-роут для StudentFunctions/Progress.js.
    Возвращает data: [ ... ] в историческом формате.
    """
    data = request.get_json() or {}
    student_id = data.get('student_id')
    if not student_id:
        return jsonify({"status": False, "error": "student_id обязателен"}), 400

    mysql_conn = None
    try:
        mysql_conn = get_db_connection()
        cursor = mysql_conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT ar.student_id, ar.exams, ar.homework, ar.tests, ar.final,
                   s.full_name
            FROM Allratings ar
            LEFT JOIN students s ON ar.student_id = s.id
            WHERE ar.student_id = %s
            LIMIT 1
        """, (student_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"status": True, "data": []})
        payload = {
            "id": row["student_id"],
            "student_id": row["student_id"],
            "full_name": row.get("full_name") or "Неизвестно",
            "homework_rate": float(row["homework"]) if row["homework"] is not None else 0,
            "test_rate": float(row["tests"]) if row["tests"] is not None else 0,
            "exam_rate": float(row["exams"]) if row["exams"] is not None else 0,
            "rate": float(row["final"]) if row["final"] is not None else 0
        }
        return jsonify({"status": True, "data": [payload]})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500
    finally:
        if mysql_conn:
            close_db_connection(mysql_conn)
