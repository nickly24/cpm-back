"""
Новая модель посещаемости: дни занятий + привязка посещаемости к ним.
Роуты запасные, старые /api/get-attendance-by-month и т.д. не трогаем.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role, require_self_or_role
from cpm_back.services.class_days import (
    get_all_attendance_types,
    create_class_day,
    list_class_days,
    get_class_day,
    delete_class_day,
    set_attendance,
    get_attendance_by_class_day,
    get_student_class_day_attendance,
)

class_days_bp = Blueprint("class_days", __name__, url_prefix="/api")


# --- Справочник типов посещения (доступен всем авторизованным для отображения) ---
@class_days_bp.route("/attendance-types", methods=["GET"])
def attendance_types_list():
    """Список типов посещения (1–8) для форм и отчётов."""
    return jsonify(get_all_attendance_types())


# --- Дни занятий (админ) ---
@class_days_bp.route("/class-days", methods=["POST"])
@require_role("admin")
def class_days_create(current_user=None):
    """
    Создать день занятий (лист посещаемости).
    Body: { "date": "YYYY-MM-DD", "comment": "опционально" }
    """
    data = request.get_json() or {}
    date_str = data.get("date")
    if not date_str:
        return jsonify({"status": False, "error": "Поле date обязательно (YYYY-MM-DD)"}), 400
    return jsonify(create_class_day(date_str, data.get("comment")))


@class_days_bp.route("/class-days", methods=["GET"])
@require_role("admin")
def class_days_list(current_user=None):
    """
    Список дней занятий за период.
    Query: ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD (оба опциональны)
    """
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    return jsonify(list_class_days(date_from, date_to))


@class_days_bp.route("/class-days/<int:class_day_id>", methods=["GET"])
@require_role("admin")
def class_days_get(class_day_id, current_user=None):
    """Один день занятий по id."""
    return jsonify(get_class_day(class_day_id))


@class_days_bp.route("/class-days/<int:class_day_id>", methods=["DELETE"])
@require_role("admin")
def class_days_delete(class_day_id, current_user=None):
    """Удалить день занятий (каскадно удалит посещаемости)."""
    return jsonify(delete_class_day(class_day_id))


# --- Посещаемость по дню занятий (админ) ---
@class_days_bp.route("/class-days/<int:class_day_id>/attendance", methods=["GET"])
@require_role("admin")
def class_day_attendance_list(class_day_id, current_user=None):
    """Список посещаемостей по дню занятий (кто и с каким типом)."""
    return jsonify(get_attendance_by_class_day(class_day_id))


@class_days_bp.route("/class-days/<int:class_day_id>/attendance", methods=["POST"])
@require_role("admin")
def class_day_attendance_set(class_day_id, current_user=None):
    """
    Добавить или обновить посещаемость студента в этот день.
    Body: { "student_id": 123, "attendance_type_id": 1, "zap_id": null }
    """
    data = request.get_json() or {}
    student_id = data.get("student_id")
    attendance_type_id = data.get("attendance_type_id")
    if student_id is None or attendance_type_id is None:
        return jsonify({"status": False, "error": "student_id и attendance_type_id обязательны"}), 400
    return jsonify(
        set_attendance(
            class_day_id,
            int(student_id),
            int(attendance_type_id),
            data.get("zap_id"),
        )
    )


# --- Посещаемость студента по дням занятий (для студента/супервизора) ---
@class_days_bp.route("/students/<int:student_id>/class-day-attendance", methods=["GET"])
@require_self_or_role("student_id", "admin", "supervisor")
def student_class_day_attendance(student_id, current_user=None):
    """
    Посещаемость студента по новой модели (дни занятий) за период.
    Query: ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
    Права: студент — только свой student_id; супервизор/админ — любой.
    """
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    return jsonify(get_student_class_day_attendance(student_id, date_from, date_to))
