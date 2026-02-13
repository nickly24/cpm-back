"""
Карточки (Platon): изученные вопросы, по теме, темы, создание темы с вопросами, удаление изученного.
Роуты без префикса /api — как в cpm-serv.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role, require_self_or_role
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

cards_bp = Blueprint('cards', __name__, url_prefix='')


@cards_bp.route('/add-learned-question', methods=['POST'])
@require_self_or_role('student_id', 'admin', 'proctor')
def add_learned_question(current_user=None):
    connection = None
    try:
        data = request.get_json()
        student_id = data.get('student_id')
        question_id = data.get('question_id')
        if not student_id or not question_id:
            return jsonify({"success": False, "error": "student_id и question_id обязательны"}), 400
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT theme_id FROM cards WHERE id = %s", (question_id,))
        question_data = cursor.fetchone()
        if not question_data:
            return jsonify({"success": False, "error": "Question not found", "question_id": question_id}), 404
        theme_id = question_data['theme_id']
        cursor.execute(
            "SELECT 1 FROM student_progress WHERE student_id = %s AND question_id = %s",
            (student_id, question_id)
        )
        if cursor.fetchone():
            return jsonify({"success": False, "message": "Record already exists"}), 409
        cursor.execute(
            "INSERT INTO student_progress (student_id, question_id, theme_id) VALUES (%s, %s, %s)",
            (student_id, question_id, theme_id)
        )
        connection.commit()
        return jsonify({
            "success": True, "message": "Record added successfully",
            "student_id": student_id, "question_id": question_id, "theme_id": theme_id
        }), 201
    except Exception as e:
        if connection:
            connection.rollback()
        return jsonify({"success": False, "error": "Internal server error", "details": str(e)}), 500
    finally:
        if connection:
            close_db_connection(connection)


@cards_bp.route('/all-cards-by-theme/<int:student_id>/<int:theme_id>', methods=['GET'])
@require_self_or_role('student_id', 'admin', 'proctor')
def all_cards_by_theme(student_id, theme_id, current_user=None):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM cards WHERE theme_id = %s", (theme_id,))
        all_cards = cursor.fetchall()
        cursor.execute(
            "SELECT question_id FROM student_progress WHERE student_id = %s AND theme_id = %s",
            (student_id, theme_id)
        )
        learned_card_ids = {row['question_id'] for row in cursor.fetchall()}
        for card in all_cards:
            card['is_learned'] = card['id'] in learned_card_ids
        return jsonify({
            "success": True, "student_id": student_id, "theme_id": theme_id,
            "cards": all_cards, "total_cards": len(all_cards),
            "learned_cards": len(learned_card_ids), "remaining_cards": len(all_cards) - len(learned_card_ids)
        })
    except Exception as e:
        return jsonify({"success": False, "error": "Internal server error", "details": str(e)}), 500
    finally:
        if connection:
            close_db_connection(connection)


@cards_bp.route('/cadrs-by-theme/<int:student_id>/<int:theme_id>', methods=['GET'])
@require_self_or_role('student_id', 'admin', 'proctor')
def cards_to_learn(student_id, theme_id, current_user=None):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT c.* FROM cards c
            WHERE c.theme_id = %s AND NOT EXISTS (
                SELECT 1 FROM student_progress sp
                WHERE sp.student_id = %s AND sp.question_id = c.id
            )
        """, (theme_id, student_id))
        cards_to_learn_list = cursor.fetchall()
        return jsonify({
            "success": True, "student_id": student_id, "theme_id": theme_id,
            "cards_to_learn": cards_to_learn_list, "count": len(cards_to_learn_list)
        })
    except Exception as e:
        return jsonify({"success": False, "error": "Internal server error", "details": str(e)}), 500
    finally:
        if connection:
            close_db_connection(connection)


@cards_bp.route('/create-theme-with-questions', methods=['POST'])
@require_role('admin')
def create_theme_with_questions(current_user=None):
    connection = None
    try:
        data = request.get_json()
        theme_name = data.get('name')
        questions = data.get('questions', [])
        if not theme_name:
            return jsonify({"success": False, "error": "Theme name is required"}), 400
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id FROM card_themes WHERE name = %s", (theme_name,))
        existing_theme = cursor.fetchone()
        if existing_theme:
            theme_id = existing_theme['id']
            message = "Theme already exists"
        else:
            cursor.execute("INSERT INTO card_themes (name) VALUES (%s)", (theme_name,))
            theme_id = cursor.lastrowid
            message = "Theme created successfully"
            connection.commit()
        added_questions = []
        for q in questions:
            question = q.get('question')
            answer = q.get('answer')
            if not question or not answer:
                continue
            cursor.execute(
                "INSERT INTO cards (question, answer, theme_id) VALUES (%s, %s, %s)",
                (question, answer, theme_id)
            )
            added_questions.append({"question": question, "answer": answer, "id": cursor.lastrowid})
        connection.commit()
        return jsonify({
            "success": True, "message": message, "theme_id": theme_id, "theme_name": theme_name,
            "added_questions": added_questions, "questions_count": len(added_questions)
        })
    except Exception as e:
        if connection:
            connection.rollback()
        return jsonify({"success": False, "error": "Internal server error", "details": str(e)}), 500
    finally:
        if connection:
            close_db_connection(connection)


@cards_bp.route('/get-themes', methods=['GET'])
def get_themes():
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True, buffered=True)
        cursor.execute("SELECT * FROM card_themes")
        themes = cursor.fetchall()
        return jsonify(themes)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if connection:
            close_db_connection(connection)


@cards_bp.route('/learned-questions/<int:student_id>/<int:theme_id>', methods=['GET'])
@require_self_or_role('student_id', 'admin', 'proctor')
def learned_questions(student_id, theme_id, current_user=None):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT c.* FROM cards c
            JOIN student_progress sp ON c.id = sp.question_id
            WHERE sp.student_id = %s AND c.theme_id = %s
        """, (student_id, theme_id))
        learned_questions_list = cursor.fetchall()
        return jsonify({
            "success": True, "student_id": student_id, "theme_id": theme_id,
            "learned_questions": learned_questions_list, "count": len(learned_questions_list)
        })
    except Exception as e:
        return jsonify({"success": False, "error": "Internal server error", "details": str(e)}), 500
    finally:
        if connection:
            close_db_connection(connection)


@cards_bp.route('/remove-learned-question/<int:student_id>/<int:question_id>', methods=['DELETE'])
@require_self_or_role('student_id', 'admin', 'proctor')
def remove_learned_question(student_id, question_id, current_user=None):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT 1 FROM student_progress WHERE student_id = %s AND question_id = %s",
            (student_id, question_id)
        )
        if not cursor.fetchone():
            return jsonify({"success": False, "message": "Record not found"}), 404
        cursor.execute(
            "DELETE FROM student_progress WHERE student_id = %s AND question_id = %s",
            (student_id, question_id)
        )
        connection.commit()
        return jsonify({
            "success": True, "message": "Record deleted successfully",
            "student_id": student_id, "question_id": question_id, "affected_rows": cursor.rowcount
        })
    except Exception as e:
        if connection:
            connection.rollback()
        return jsonify({"success": False, "error": "Internal server error", "details": str(e)}), 500
    finally:
        if connection:
            close_db_connection(connection)
