"""
Массовый импорт manual-карточек из Excel.
"""
from flask import Blueprint, jsonify, request

from cpm_back.auth import require_role
from cpm_back.services.card_import.build_preview import IMPORT_TYPE
from cpm_back.services.card_import.sessions import (
    get_session,
    get_session_preview_for_commit,
    parse_file_to_session,
    update_session,
)
from cpm_back.services.user_import.import_jobs import create_import_job, enqueue_import_job

card_import_bp = Blueprint("card_import", __name__, url_prefix="/api/card-import")


def _schema_status(answer, *, ok=200, missing=503, fail=400):
    if answer.get("code") == "user_import_schema_missing":
        return jsonify(answer), missing
    if answer.get("status"):
        return jsonify(answer), ok
    return jsonify(answer), fail


@card_import_bp.route("/parse", methods=["POST"])
@require_role("admin")
def parse_upload(current_user=None):
    upload = request.files.get("file")
    direction_id = request.form.get("direction_id")
    theme_id = request.form.get("theme_id")
    if not upload:
        return jsonify({"status": False, "error": "Файл не передан"}), 400
    if not direction_id:
        return jsonify({"status": False, "error": "Выберите направление"}), 400
    if not theme_id:
        return jsonify({"status": False, "error": "Выберите раздел"}), 400

    answer = parse_file_to_session(
        upload.read(),
        upload.filename,
        direction_id,
        theme_id,
        created_by=current_user.get("id") if current_user else None,
        created_by_name=current_user.get("full_name") if current_user else None,
    )
    return _schema_status(answer)


@card_import_bp.route("/sessions/<int:session_id>", methods=["GET"])
@require_role("admin")
def session_detail(session_id, current_user=None):
    session = get_session(session_id)
    if not session:
        return jsonify({"status": False, "error": "Сессия не найдена или истекла"}), 404
    return jsonify(session)


@card_import_bp.route("/sessions/<int:session_id>", methods=["PUT"])
@require_role("admin")
def session_update(session_id, current_user=None):
    data = request.get_json() or {}
    preview = data.get("preview")
    if not isinstance(preview, dict):
        return jsonify({"status": False, "error": "Поле preview обязательно"}), 400
    return _schema_status(update_session(session_id, preview))


@card_import_bp.route("/sessions/<int:session_id>/commit", methods=["POST"])
@require_role("admin")
def session_commit(session_id, current_user=None):
    prepared = get_session_preview_for_commit(session_id)
    if not prepared.get("status"):
        return jsonify(prepared), 400

    try:
        job = create_import_job(
            session_id,
            prepared["preview"],
            import_type=IMPORT_TYPE,
            created_by=current_user.get("id") if current_user else None,
            created_by_name=current_user.get("full_name") if current_user else None,
        )
        enqueue_import_job(job["id"])
        return jsonify({"status": True, "message": "Импорт карточек запущен", "job": job})
    except ValueError as exc:
        return jsonify({"status": False, "error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"status": False, "error": str(exc)}), 500
