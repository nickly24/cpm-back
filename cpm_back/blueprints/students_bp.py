"""
Студенты: фильтр по группе, список, по ID, добавление, редактирование, валидация по TG.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role, require_self_or_role
from cpm_back.services.serv import (
    get_student_ids_and_names_by_group,
    get_all_students,
    get_student_by_id,
    add_student,
    edit_student,
    validate_student_by_tg_name,
)

students_bp = Blueprint('students', __name__, url_prefix='/api')


@students_bp.route('/student-group-filter', methods=['POST'])
@require_role('admin', 'proctor')
def group_filter(current_user=None):
    data = request.get_json()
    return jsonify(get_student_ids_and_names_by_group(data.get('id')))


@students_bp.route('/get-students')
@require_role('admin')
def list_students(current_user=None):
    return jsonify(get_all_students())


@students_bp.route('/get-class-name-by-studID', methods=['POST'])
@require_self_or_role('student_id', 'admin', 'proctor')
def by_id(current_user=None):
    data = request.get_json()
    student_id = data.get('student_id')
    if not student_id:
        return jsonify({"status": False, "error": "Отсутствует student_id"}), 400
    return jsonify(get_student_by_id(student_id))


@students_bp.route('/add-student', methods=['POST'])
@require_role('admin')
def add(current_user=None):
    data = request.get_json()
    if not data:
        return jsonify({"status": False, "error": "Данные не предоставлены"}), 400
    full_name = data.get('full_name')
    class_number = data.get('class')
    tg_name = data.get('tg_name')
    if not full_name:
        return jsonify({"status": False, "error": "Поле 'full_name' обязательно"}), 400
    if not class_number:
        return jsonify({"status": False, "error": "Поле 'class' обязательно"}), 400
    try:
        class_number = int(class_number)
    except (ValueError, TypeError):
        return jsonify({"status": False, "error": "Поле 'class' должно быть числом"}), 400
    answer = add_student(full_name, class_number, tg_name)
    return jsonify(answer), 200 if answer.get('status') else 400


@students_bp.route('/edit-student', methods=['PUT'])
@require_role('admin')
def edit(current_user=None):
    data = request.get_json()
    if not data or not data.get('student_id'):
        return jsonify({"status": False, "error": "student_id обязателен"}), 400
    if all(data.get(f) is None for f in ['full_name', 'class', 'group_id', 'tg_name']):
        return jsonify({"status": False, "error": "Укажите хотя бы одно поле для обновления"}), 400
    class_number = data.get('class')
    if class_number is not None:
        try:
            class_number = int(class_number)
        except (ValueError, TypeError):
            return jsonify({"status": False, "error": "Поле 'class' должно быть числом"}), 400
    answer = edit_student(
        data.get('student_id'),
        data.get('full_name'),
        class_number,
        data.get('group_id'),
        data.get('tg_name')
    )
    return jsonify(answer), 200 if answer.get('status') else 400


@students_bp.route('/validate-student-by-tg', methods=['POST'])
def validate_tg():
    data = request.get_json()
    if not data or not data.get('tg_name'):
        return jsonify({"status": False, "error": "Поле 'tg_name' обязательно"}), 400
    answer = validate_student_by_tg_name(data.get('tg_name'))
    return jsonify(answer), 200 if answer.get('status') else 404
