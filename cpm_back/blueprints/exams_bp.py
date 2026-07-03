"""
Экзамены и посещаемость (exam): список экзаменов, сессии, посещаемость студента за месяц.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role, require_self_or_role
from cpm_back.services.exam.get_exams import (
    delete_exam,
    get_all_exams,
    get_all_exams_paginated,
    get_exam_delete_preview,
    get_exam_session,
    get_exam_sessions_by_student,
    get_exam_sessions_by_student_paginated,
    get_all_exam_sessions,
    get_exam_sessions_by_exam,
    get_exam_sessions_by_exam_paginated,
)
from cpm_back.services.exam.get_student_attendance import get_student_attendance

exams_bp = Blueprint('exams', __name__, url_prefix='')


def _uses_pagination():
    return (
        request.args.get('page') is not None
        or request.args.get('limit') is not None
    )


@exams_bp.route('/get-all-exams', methods=['GET'])
def list_exams():
    if _uses_pagination():
        return jsonify(
            get_all_exams_paginated(
                page_raw=request.args.get('page'),
                limit_raw=request.args.get('limit'),
                search=request.args.get('search'),
                sort=request.args.get('sort', 'date'),
            )
        )
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
    if _uses_pagination():
        return jsonify(
            get_exam_sessions_by_student_paginated(
                student_id=student_id,
                page_raw=request.args.get('page'),
                limit_raw=request.args.get('limit'),
                grade=request.args.get('grade'),
                sort=request.args.get('sort', 'exam_date'),
            )
        )
    return jsonify(get_exam_sessions_by_student(student_id))


@exams_bp.route('/get-all-exam-sessions', methods=['GET'])
@require_role('admin')
def all_sessions(current_user=None):
    return jsonify(get_all_exam_sessions())


@exams_bp.route('/get-exam-sessions/<exam_id>', methods=['GET'])
@require_role('admin')
def sessions_by_exam(exam_id, current_user=None):
    if _uses_pagination():
        return jsonify(
            get_exam_sessions_by_exam_paginated(
                exam_id=exam_id,
                page_raw=request.args.get('page'),
                limit_raw=request.args.get('limit'),
                search=request.args.get('search'),
                sort=request.args.get('sort', 'student_name'),
            )
        )
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


@exams_bp.route('/exams/<exam_id>/delete-preview', methods=['GET'])
@require_role('admin')
def delete_preview(exam_id, current_user=None):
    preview = get_exam_delete_preview(exam_id)
    if not preview:
        return jsonify({'error': 'Exam not found'}), 404
    return jsonify(preview)


@exams_bp.route('/exams/<exam_id>', methods=['DELETE'])
@require_role('admin')
def delete(exam_id, current_user=None):
    try:
        result = delete_exam(exam_id)
    except Exception as exc:
        return jsonify({'error': 'Failed to delete exam', 'message': str(exc)}), 500

    if not result:
        return jsonify({'error': 'Exam not found'}), 404

    return jsonify({
        'message': 'Exam and related sessions deleted successfully',
        'examId': int(exam_id) if str(exam_id).isdigit() else exam_id,
        'sessionsDeleted': result['sessionsDeleted'],
    })
