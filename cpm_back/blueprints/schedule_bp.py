"""
Расписание занятий (MongoDB): получить, добавить, редактировать, удалить.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role
from cpm_back.services.serv.schedule_manager import ScheduleManager

schedule_bp = Blueprint('schedule', __name__, url_prefix='/api')


def _schedule_result(result, success_code=200, fail_code=400):
    code = success_code if result.get('status') else fail_code
    return jsonify(result), code


@schedule_bp.route('/schedule', methods=['GET'])
@require_role('admin', 'student')
def get_schedule(current_user=None):
    manager = ScheduleManager()
    result = manager.get_all_schedule()
    return _schedule_result(result, fail_code=500)


@schedule_bp.route('/schedule', methods=['POST'])
@require_role('admin')
def add_lesson(current_user=None):
    data = request.get_json()
    if not data:
        return jsonify({"status": False, "error": "Данные не предоставлены"}), 400
    manager = ScheduleManager()
    result = manager.add_lesson(data)
    return _schedule_result(result)


@schedule_bp.route('/schedule/<lesson_id>', methods=['PUT'])
@require_role('admin')
def edit_lesson(lesson_id, current_user=None):
    data = request.get_json()
    if not data:
        return jsonify({"status": False, "error": "Данные не предоставлены"}), 400
    manager = ScheduleManager()
    result = manager.edit_lesson(lesson_id, data)
    return _schedule_result(result)


@schedule_bp.route('/schedule/<lesson_id>', methods=['DELETE'])
@require_role('admin')
def delete_lesson(lesson_id, current_user=None):
    manager = ScheduleManager()
    result = manager.delete_lesson(lesson_id)
    return _schedule_result(result)
