"""
Календарное расписание занятий (MongoDB): получить, добавить, редактировать, удалить.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role
from cpm_back.services.serv.schedule_manager import ScheduleManager

schedule_bp = Blueprint("schedule", __name__, url_prefix="/api")


def _schedule_result(result, success_code=200, fail_code=400):
    code = success_code if result.get("status") else fail_code
    return jsonify(result), code


@schedule_bp.route("/schedule", methods=["GET"])
@require_role("admin", "student")
def get_schedule(current_user=None):
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    if not date_from or not date_to:
        return jsonify({
            "status": False,
            "error": "Параметры date_from и date_to обязательны (формат YYYY-MM-DD)",
        }), 400

    user = current_user or {}
    manager = ScheduleManager()
    result = manager.get_schedule(
        date_from=date_from,
        date_to=date_to,
        role=user.get("role", ""),
        user_id=user.get("id"),
    )
    if result.get("status"):
        return _schedule_result(result)
    error = result.get("error") or ""
    fail_code = 500 if error.startswith("Ошибка при загрузке") else 400
    return _schedule_result(result, fail_code=fail_code)


@schedule_bp.route("/schedule", methods=["POST"])
@require_role("admin")
def add_lesson(current_user=None):
    data = request.get_json()
    if not data:
        return jsonify({"status": False, "error": "Данные не предоставлены"}), 400
    manager = ScheduleManager()
    result = manager.add_lesson(data)
    return _schedule_result(result)


@schedule_bp.route("/schedule/bulk", methods=["POST"])
@require_role("admin")
def bulk_save_schedule(current_user=None):
    data = request.get_json()
    if not data:
        return jsonify({"status": False, "error": "Данные не предоставлены"}), 400
    manager = ScheduleManager()
    result = manager.bulk_save(data)
    return _schedule_result(result)


@schedule_bp.route("/schedule/<lesson_id>", methods=["PUT"])
@require_role("admin")
def edit_lesson(lesson_id, current_user=None):
    data = request.get_json()
    if not data:
        return jsonify({"status": False, "error": "Данные не предоставлены"}), 400
    manager = ScheduleManager()
    result = manager.edit_lesson(lesson_id, data)
    return _schedule_result(result)


@schedule_bp.route("/schedule/<lesson_id>", methods=["DELETE"])
@require_role("admin")
def delete_lesson(lesson_id, current_user=None):
    manager = ScheduleManager()
    result = manager.delete_lesson(lesson_id)
    return _schedule_result(result)
