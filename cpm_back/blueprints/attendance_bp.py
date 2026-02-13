"""
Посещаемость: по дате, по месяцу, добавление.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role
from cpm_back.services.serv import get_attendance_by_date, get_attendance_diary, add_attendance

attendance_bp = Blueprint('attendance', __name__, url_prefix='/api')


@attendance_bp.route('/get-attendance-by-date', methods=['POST'])
@require_role('admin')
def by_date(current_user=None):
    data = request.get_json()
    date = data.get('date')
    if not date:
        return jsonify({'status': False, 'error': 'Поле "date" обязательно'}), 400
    return jsonify(get_attendance_by_date(date))


@attendance_bp.route('/get-attendance-by-month', methods=['POST'])
@require_role('admin')
def by_month(current_user=None):
    data = request.get_json()
    month = data.get('month')
    year = data.get('year')
    if not month or not year:
        return jsonify({'status': False, 'error': 'month и year обязательны'}), 400
    return jsonify(get_attendance_diary(year, month))


@attendance_bp.route('/add-attendance', methods=['POST'])
@require_role('admin')
def add(current_user=None):
    data = request.get_json()
    student_id = data.get('studentId')
    date = data.get('date')
    if not student_id or not date:
        return jsonify({'status': False, 'error': 'studentId и date обязательны'}), 400
    attendance_rate = data.get('attendance_rate', 1)
    zap_id = data.get('zap_id')
    return jsonify(add_attendance(student_id, date, attendance_rate, zap_id))
