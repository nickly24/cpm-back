"""
Массовый импорт пользователей из Excel.
"""
from flask import Blueprint, jsonify, request

from cpm_back.auth import require_role
from cpm_back.services.user_import.import_jobs import (
    create_import_job,
    enqueue_import_job,
    get_active_import_job_id,
    get_import_job,
    get_job_report,
    has_active_import_job,
    list_import_jobs,
)
from cpm_back.services.user_import.sessions import (
    get_session,
    get_session_preview_for_commit,
    parse_file_to_session,
    update_session,
)

user_import_bp = Blueprint("user_import", __name__, url_prefix="/api/user-import")


def _schema_status(answer, *, ok=200, missing=503, fail=400):
    if answer.get("code") == "user_import_schema_missing":
        return jsonify(answer), missing
    if answer.get("status"):
        return jsonify(answer), ok
    return jsonify(answer), fail


@user_import_bp.route("/parse", methods=["POST"])
@require_role("admin")
def parse_upload(current_user=None):
    upload = request.files.get("file")
    if not upload:
        return jsonify({"status": False, "error": "Файл не передан"}), 400

    answer = parse_file_to_session(
        upload.read(),
        upload.filename,
        created_by=current_user.get("id") if current_user else None,
        created_by_name=current_user.get("full_name") if current_user else None,
    )
    return _schema_status(answer)


@user_import_bp.route("/sessions/<int:session_id>", methods=["GET"])
@require_role("admin")
def session_detail(session_id, current_user=None):
    session = get_session(session_id)
    if not session:
        return jsonify({"status": False, "error": "Сессия не найдена или истекла"}), 404
    return jsonify(session)


@user_import_bp.route("/sessions/<int:session_id>", methods=["PUT"])
@require_role("admin")
def session_update(session_id, current_user=None):
    data = request.get_json() or {}
    preview = data.get("preview")
    if not isinstance(preview, dict):
        return jsonify({"status": False, "error": "Поле preview обязательно"}), 400
    return _schema_status(update_session(session_id, preview))


@user_import_bp.route("/sessions/<int:session_id>/commit", methods=["POST"])
@require_role("admin")
def session_commit(session_id, current_user=None):
    prepared = get_session_preview_for_commit(session_id)
    if not prepared.get("status"):
        return jsonify(prepared), 400

    try:
        job = create_import_job(
            session_id,
            prepared["preview"],
            created_by=current_user.get("id") if current_user else None,
            created_by_name=current_user.get("full_name") if current_user else None,
        )
        enqueue_import_job(job["id"])
        return jsonify(
            {
                "status": True,
                "message": "Импорт запущен",
                "job": job,
            }
        )
    except ValueError as exc:
        return jsonify({"status": False, "error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"status": False, "error": str(exc)}), 500


@user_import_bp.route("/jobs", methods=["GET"])
@require_role("admin")
def jobs_list(current_user=None):
    limit = request.args.get("limit", 50)
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 50

    jobs = list_import_jobs(limit=limit)
    return jsonify(
        {
            "status": True,
            "jobs": jobs,
            "active_job_id": get_active_import_job_id(),
            "has_active": has_active_import_job(),
            "total": len(jobs),
        }
    )


@user_import_bp.route("/jobs/<int:job_id>", methods=["GET"])
@require_role("admin")
def job_detail(job_id, current_user=None):
    job = get_import_job(job_id)
    if not job:
        return jsonify({"status": False, "error": "Задача не найдена"}), 404
    return jsonify({"status": True, "job": job})


@user_import_bp.route("/jobs/<int:job_id>/report", methods=["GET"])
@require_role("admin")
def job_report(job_id, current_user=None):
    report = get_job_report(job_id)
    if not report:
        return jsonify({"status": False, "error": "Отчёт не найден"}), 404
    if report.get("status") != "completed":
        return jsonify({"status": False, "error": "Отчёт доступен только для успешных загрузок"}), 400
    return jsonify({"status": True, "report": report})
