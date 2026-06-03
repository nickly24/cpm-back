"""
Школы: справочник и фильтрация учеников по школе.
"""
from flask import Blueprint, jsonify, request

from cpm_back.auth import require_role
from cpm_back.services.serv import (
    add_school,
    edit_school,
    get_all_schools,
    get_school_by_id,
    get_student_ids_and_names_by_school,
    get_unassigned_students_by_school,
)

schools_bp = Blueprint("schools", __name__, url_prefix="/api")


def _schools_http_status(answer, *, ok=200, missing=503, fail=400):
    if answer.get("code") == "schools_schema_missing":
        return jsonify(answer), missing
    if answer.get("status"):
        return jsonify(answer), ok
    return jsonify(answer), fail


@schools_bp.route("/get-schools", methods=["GET"])
@require_role("admin")
def list_schools(current_user=None):
    active_only = request.args.get("active") == "1"
    return _schools_http_status(get_all_schools(active_only=active_only))


@schools_bp.route("/get-school/<int:school_id>", methods=["GET"])
@require_role("admin")
def school_by_id(school_id, current_user=None):
    answer = get_school_by_id(school_id)
    return _schools_http_status(answer, ok=200, fail=404)


@schools_bp.route("/add-school", methods=["POST"])
@require_role("admin")
def create_school(current_user=None):
    data = request.get_json() or {}
    answer = add_school(
        data.get("name"),
        short_name=data.get("short_name"),
        notes=data.get("notes"),
    )
    return _schools_http_status(answer, ok=200, fail=400)


@schools_bp.route("/edit-school", methods=["PUT"])
@require_role("admin")
def update_school(current_user=None):
    data = request.get_json() or {}
    school_id = data.get("school_id")
    if not school_id:
        return jsonify({"status": False, "error": "school_id обязателен"}), 400

    if all(data.get(field) is None for field in ("name", "short_name", "notes", "is_active")):
        return jsonify({"status": False, "error": "Укажите хотя бы одно поле для обновления"}), 400

    answer = edit_school(
        school_id,
        name=data.get("name"),
        short_name=data.get("short_name"),
        notes=data.get("notes"),
        is_active=data.get("is_active"),
    )
    return _schools_http_status(answer, ok=200, fail=400)


@schools_bp.route("/student-school-filter", methods=["POST"])
@require_role("admin")
def school_filter(current_user=None):
    data = request.get_json() or {}
    school_id = data.get("id") or data.get("school_id")
    if not school_id:
        return jsonify({"status": False, "error": "id (school_id) обязателен"}), 400
    return _schools_http_status(get_student_ids_and_names_by_school(school_id))


@schools_bp.route("/get-unsigned-students-by-school", methods=["GET"])
@require_role("admin")
def unsigned_students(current_user=None):
    return jsonify(get_unassigned_students_by_school())
