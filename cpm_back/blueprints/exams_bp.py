"""
Экзамены и посещаемость (exam): список экзаменов, сессии, посещаемость студента за месяц.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role, require_self_or_role
from cpm_back.services.exam.get_exams import (
    get_all_exams,
    get_exam_session,
    get_exam_sessions_by_student,
    get_all_exam_sessions,
    get_exam_sessions_by_exam,
)
from cpm_back.services.exam.get_student_attendance import get_student_attendance

exams_bp = Blueprint('exams', __name__, url_prefix='')


@exams_bp.route('/get-all-exams', methods=['GET'])
def list_exams():
    return jsonify(get_all_exams())


@exams_bp.route('/get-exam-session', methods=['POST'])
@require_self_or_role('student_id', 'admin')
def exam_session(current_user=None):
    data = request.get_json()
    student_id = data.get('student_id')
    exam_id = data.get('exam_id')
    if not student_id or not exam_id:
        return jsonify({"status": False, "error": "Отсутствуют обязательные поля: student_id, exam_id"}), 400
    return jsonify(get_exam_session(student_id, exam_id))


@exams_bp.route('/get-student-exam-sessions/<student_id>', methods=['GET'])
@require_self_or_role('student_id', 'admin')
def student_sessions(student_id, current_user=None):
    return jsonify(get_exam_sessions_by_student(student_id))


@exams_bp.route('/get-all-exam-sessions', methods=['GET'])
@require_role('admin')
def all_sessions(current_user=None):
    return jsonify(get_all_exam_sessions())


@exams_bp.route('/get-exam-sessions/<exam_id>', methods=['GET'])
@require_role('admin')
def sessions_by_exam(exam_id, current_user=None):
    return jsonify(get_exam_sessions_by_exam(exam_id))


@exams_bp.route('/get-attendance', methods=['POST'])
@require_self_or_role('student_id', 'admin')
def attendance(current_user=None):
    """Посещаемость студента за месяц. JSON: {"student_id": "123", "year_month": "2025-01"}"""
    data = request.get_json()
    student_id = data.get('student_id')
    year_month = data.get('year_month')
    if not student_id or not year_month:
        return jsonify({"status": False, "error": "Отсутствуют обязательные поля: student_id, year_month"}), 400
    return jsonify(get_student_attendance(student_id, year_month))
