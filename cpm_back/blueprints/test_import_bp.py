"""
JSON import for internal CPM tests.
"""
from flask import Blueprint, jsonify, request

from cpm_back.auth import require_role
from cpm_back.services.test_import.json_import import build_preview, commit_import

test_import_bp = Blueprint("test_import", __name__, url_prefix="/api/test-import")


@test_import_bp.route("/preview", methods=["POST"])
@require_role("admin")
def preview_test_import(current_user=None):
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({
            "status": False,
            "error": "Передайте JSON-файл теста",
            "preview": {},
            "summary": {
                "questionsTotal": 0,
                "totalPoints": 0,
                "singleCount": 0,
                "multipleCount": 0,
                "textCount": 0,
                "errorsCount": 1,
            },
            "errors": [{"path": "$", "message": "JSON не распознан"}],
        }), 400
    answer = build_preview(payload)
    return jsonify(answer)


@test_import_bp.route("/commit", methods=["POST"])
@require_role("admin")
def commit_test_import(current_user=None):
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": False, "error": "Передайте JSON-файл теста"}), 400
    answer = commit_import(payload)
    return jsonify(answer), 200 if answer.get("status") else 400
