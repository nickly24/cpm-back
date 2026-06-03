"""
Карточки (Platon): изученные вопросы, по теме, темы, создание темы с вопросами, удаление изученного.
Роуты без префикса /api — как в cpm-serv.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role, require_self_or_role
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from cpm_back.services.cards import (
    get_training_sections as fetch_training_sections,
    get_training_tree,
    get_themes,
    create_training_section,
    create_theme_with_questions,
)

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
def create_theme_with_questions_route(current_user=None):
    data = request.get_json() or {}
    theme_name = data.get('name')
    section_id = data.get('section_id')
    questions = data.get('questions', [])

    if not section_id:
        return jsonify({"success": False, "error": "section_id обязателен"}), 400

    result = create_theme_with_questions(theme_name, section_id, questions)
    if not result.get('success'):
        status = 404 if result.get('error') == 'Section not found' else 400
        if result.get('details'):
            return jsonify(result), 500
        return jsonify(result), status
    return jsonify(result)


@cards_bp.route('/create-training-section', methods=['POST'])
@require_role('admin')
def create_training_section_route(current_user=None):
    data = request.get_json() or {}
    name = data.get('name')
    sort_order = data.get('sort_order', 0)

    result = create_training_section(name, sort_order)
    if not result.get('success'):
        status = 409 if 'уже существует' in result.get('error', '') else 400
        return jsonify(result), status
    return jsonify(result), 201


@cards_bp.route('/get-training-sections', methods=['GET'])
def get_training_sections_route():
    result = fetch_training_sections()
    if not result.get('success'):
        return jsonify(result), 500
    return jsonify(result)


@cards_bp.route('/get-training-tree/<int:student_id>', methods=['GET'])
@require_self_or_role('student_id', 'admin', 'proctor')
def get_training_tree_route(student_id, current_user=None):
    result = get_training_tree(student_id)
    if not result.get('success'):
        return jsonify(result), 500
    return jsonify(result)


@cards_bp.route('/get-themes', methods=['GET'])
def get_themes_route():
    section_id = request.args.get('section_id', type=int)
    themes = get_themes(section_id=section_id)
    if isinstance(themes, dict) and not themes.get('success', True):
        return jsonify(themes), 500
    return jsonify(themes)


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
