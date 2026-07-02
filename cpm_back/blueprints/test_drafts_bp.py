from flask import Blueprint, jsonify, request

from cpm_back.auth import require_role
from cpm_back.services.exam.test_drafts import (
    create_test_draft,
    create_test_draft_from_test,
    get_test_draft,
    list_test_drafts,
    lock_test_draft,
    publish_test_draft,
    unlock_test_draft,
    update_test_draft,
)


test_drafts_bp = Blueprint("test_drafts", __name__, url_prefix="")


@test_drafts_bp.route("/test-drafts", methods=["GET"])
@require_role("admin")
def drafts_list(current_user=None):
    status = request.args.get("status", "active")
    return jsonify(list_test_drafts(status=status))


@test_drafts_bp.route("/test-drafts", methods=["POST"])
@require_role("admin")
def drafts_create(current_user=None):
    draft = create_test_draft(request.get_json(silent=True) or {}, current_user=current_user)
    return jsonify(draft), 201


@test_drafts_bp.route("/test-drafts/from-test/<test_id>", methods=["POST"])
@require_role("admin")
def drafts_from_test(test_id, current_user=None):
    draft = create_test_draft_from_test(test_id, current_user=current_user)
    if not draft:
        return jsonify({"error": "test_not_found"}), 404
    return jsonify(draft), 201


@test_drafts_bp.route("/test-drafts/<draft_id>", methods=["GET"])
@require_role("admin")
def drafts_get(draft_id, current_user=None):
    draft = get_test_draft(draft_id)
    if not draft:
        return jsonify({"error": "draft_not_found"}), 404
    return jsonify(draft)


@test_drafts_bp.route("/test-drafts/<draft_id>", methods=["PUT"])
@require_role("admin")
def drafts_update(draft_id, current_user=None):
    draft = update_test_draft(draft_id, request.get_json(silent=True) or {}, current_user=current_user)
    if not draft:
        return jsonify({"error": "draft_not_found"}), 404
    return jsonify(draft)


@test_drafts_bp.route("/test-drafts/<draft_id>/lock", methods=["POST"])
@require_role("admin")
def drafts_lock(draft_id, current_user=None):
    payload = request.get_json(silent=True) or {}
    result = lock_test_draft(draft_id, current_user=current_user, force=bool(payload.get("force")))
    if not result.get("success"):
        code = 409 if result.get("error") == "locked" else 404
        return jsonify(result), code
    return jsonify(result)


@test_drafts_bp.route("/test-drafts/<draft_id>/unlock", methods=["POST"])
@require_role("admin")
def drafts_unlock(draft_id, current_user=None):
    result = unlock_test_draft(draft_id, current_user=current_user)
    if not result.get("success"):
        return jsonify(result), 409 if result.get("error") == "locked_by_other" else 404
    return jsonify(result)


@test_drafts_bp.route("/test-drafts/<draft_id>/publish", methods=["POST"])
@require_role("admin")
def drafts_publish(draft_id, current_user=None):
    result, code = publish_test_draft(draft_id, current_user=current_user)
    return jsonify(result), code
