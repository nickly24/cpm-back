"""
Карточки (тренировки v2): направления, разделы, батчи, прогресс.
"""
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role, require_self_or_role
from cpm_back.services.cards import (
    get_training_tree,
    get_section_study_view,
    get_section_batch_cards,
    update_section_study_settings,
    mark_section_card_learned,
    unmark_card_learned,
    get_admin_training_catalog,
    create_training_theme,
    update_training_theme,
    delete_training_theme,
    create_theme_with_questions,
    get_cards_by_theme_admin,
    create_card,
    update_card,
    delete_card,
)

cards_bp = Blueprint("cards", __name__, url_prefix="")


def _not_found_status(result):
    err = result.get("error", "")
    if err in ("Section not found", "Theme not found", "Card not found", "Batch not found"):
        return 404
    if err == "answers_hidden":
        return 403
    if "уже" in err.lower() or "already" in err.lower():
        return 409
    return 400


@cards_bp.route("/get-training-tree/<int:student_id>", methods=["GET"])
@require_self_or_role("student_id", "admin", "proctor")
def get_training_tree_route(student_id, current_user=None):
    result = get_training_tree(student_id)
    if not result.get("success"):
        return jsonify(result), 500
    return jsonify(result)


@cards_bp.route(
    "/section-study/<int:student_id>/<string:section_kind>/<string:section_ref_id>",
    methods=["GET"],
)
@require_self_or_role("student_id", "admin", "proctor")
def section_study_view_route(student_id, section_kind, section_ref_id, current_user=None):
    if section_kind not in ("manual", "test"):
        return jsonify({"success": False, "error": "Invalid section kind"}), 400
    result = get_section_study_view(student_id, section_kind, section_ref_id)
    if not result.get("success"):
        return jsonify(result), _not_found_status(result)
    return jsonify(result)


@cards_bp.route(
    "/section-study-settings/<int:student_id>/<string:section_kind>/<string:section_ref_id>",
    methods=["PUT"],
)
@require_self_or_role("student_id", "admin", "proctor")
def section_study_settings_route(
    student_id, section_kind, section_ref_id, current_user=None
):
    if section_kind not in ("manual", "test"):
        return jsonify({"success": False, "error": "Invalid section kind"}), 400
    data = request.get_json() or {}
    result = update_section_study_settings(
        student_id, section_kind, section_ref_id, data
    )
    if not result.get("success"):
        return jsonify(result), 400
    return jsonify(result)


@cards_bp.route(
    "/section-batch/<int:student_id>/<string:section_kind>/<string:section_ref_id>/<int:batch_index>",
    methods=["GET"],
)
@require_self_or_role("student_id", "admin", "proctor")
def section_batch_route(
    student_id, section_kind, section_ref_id, batch_index, current_user=None
):
    if section_kind not in ("manual", "test"):
        return jsonify({"success": False, "error": "Invalid section kind"}), 400
    study_mode = request.args.get("study_mode")
    result = get_section_batch_cards(
        student_id,
        section_kind,
        section_ref_id,
        batch_index,
        study_mode=study_mode,
    )
    if not result.get("success"):
        return jsonify(result), _not_found_status(result)
    return jsonify(result)


@cards_bp.route("/mark-card-learned", methods=["POST"])
@require_self_or_role("student_id", "admin", "proctor")
def mark_card_learned_route(current_user=None):
    data = request.get_json() or {}
    student_id = data.get("student_id")
    section_kind = data.get("section_kind")
    section_ref_id = data.get("section_ref_id")
    card_ref = data.get("card_ref")
    fingerprint = data.get("content_fingerprint")

    if not all([student_id, section_kind, section_ref_id, card_ref, fingerprint]):
        return jsonify(
            {
                "success": False,
                "error": "student_id, section_kind, section_ref_id, card_ref, content_fingerprint обязательны",
            }
        ), 400

    if section_kind not in ("manual", "test"):
        return jsonify({"success": False, "error": "Invalid section kind"}), 400

    result = mark_section_card_learned(
        student_id, section_kind, str(section_ref_id), card_ref, fingerprint
    )
    if not result.get("success"):
        return jsonify(result), _not_found_status(result)
    return jsonify(result), 201


@cards_bp.route(
    "/mark-card-learned/<int:student_id>/<path:card_ref>", methods=["DELETE"]
)
@require_self_or_role("student_id", "admin", "proctor")
def unmark_card_learned_route(student_id, card_ref, current_user=None):
    result = unmark_card_learned(student_id, card_ref)
    if not result.get("success"):
        return jsonify(result), _not_found_status(result)
    return jsonify(result)


@cards_bp.route("/get-admin-training-catalog", methods=["GET"])
@require_role("admin")
def get_admin_training_catalog_route(current_user=None):
    result = get_admin_training_catalog()
    if not result.get("success"):
        return jsonify(result), 500
    return jsonify(result)


@cards_bp.route("/create-training-theme", methods=["POST"])
@require_role("admin")
def create_training_theme_route(current_user=None):
    data = request.get_json() or {}
    direction_id = data.get("direction_id") or data.get("section_id")
    result = create_training_theme(data.get("name"), direction_id)
    if not result.get("success"):
        return jsonify(result), _not_found_status(result)
    return jsonify(result), 201


@cards_bp.route("/training-theme/<int:theme_id>", methods=["PUT"])
@require_role("admin")
def update_training_theme_route(theme_id, current_user=None):
    data = request.get_json() or {}
    result = update_training_theme(
        theme_id,
        name=data.get("name"),
        direction_id=data.get("direction_id") or data.get("section_id"),
    )
    if not result.get("success"):
        return jsonify(result), _not_found_status(result)
    return jsonify(result)


@cards_bp.route("/training-theme/<int:theme_id>", methods=["DELETE"])
@require_role("admin")
def delete_training_theme_route(theme_id, current_user=None):
    result = delete_training_theme(theme_id)
    if not result.get("success"):
        return jsonify(result), _not_found_status(result)
    return jsonify(result)


@cards_bp.route("/admin-cards-by-theme/<int:theme_id>", methods=["GET"])
@require_role("admin")
def admin_cards_by_theme_route(theme_id, current_user=None):
    result = get_cards_by_theme_admin(theme_id)
    if not result.get("success"):
        return jsonify(result), _not_found_status(result)
    return jsonify(result)


@cards_bp.route("/create-card", methods=["POST"])
@require_role("admin")
def create_card_route(current_user=None):
    data = request.get_json() or {}
    result = create_card(
        data.get("theme_id"),
        data.get("question"),
        data.get("answer"),
        sort_order=data.get("sort_order"),
    )
    if not result.get("success"):
        return jsonify(result), _not_found_status(result)
    return jsonify(result), 201


@cards_bp.route("/card/<int:card_id>", methods=["PUT"])
@require_role("admin")
def update_card_route(card_id, current_user=None):
    data = request.get_json() or {}
    result = update_card(
        card_id,
        question=data.get("question"),
        answer=data.get("answer"),
        sort_order=data.get("sort_order"),
    )
    if not result.get("success"):
        return jsonify(result), _not_found_status(result)
    return jsonify(result)


@cards_bp.route("/card/<int:card_id>", methods=["DELETE"])
@require_role("admin")
def delete_card_route(card_id, current_user=None):
    result = delete_card(card_id)
    if not result.get("success"):
        return jsonify(result), _not_found_status(result)
    return jsonify(result)


@cards_bp.route("/create-theme-with-questions", methods=["POST"])
@require_role("admin")
def create_theme_with_questions_route(current_user=None):
    data = request.get_json() or {}
    direction_id = data.get("direction_id") or data.get("section_id")
    if not direction_id:
        return jsonify({"success": False, "error": "direction_id обязателен"}), 400

    result = create_theme_with_questions(
        data.get("name"), direction_id, data.get("questions", [])
    )
    if not result.get("success"):
        status = 404 if result.get("error") == "Direction not found" else 400
        if result.get("details"):
            return jsonify(result), 500
        return jsonify(result), status
    return jsonify(result)
