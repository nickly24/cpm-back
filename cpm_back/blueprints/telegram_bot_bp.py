from flask import Blueprint, jsonify, request

from cpm_back.auth import require_role
from cpm_back.services.telegram_bot import (
    get_settings,
    get_status,
    restart_bot,
    save_settings,
    start_bot,
    stop_bot,
)

telegram_bot_bp = Blueprint("telegram_bot", __name__, url_prefix="/api/telegram-bot")


@telegram_bot_bp.route("/status", methods=["GET"])
@require_role("admin")
def status(current_user=None):
    return jsonify(get_status())


@telegram_bot_bp.route("/settings", methods=["GET"])
@require_role("admin")
def settings(current_user=None):
    return jsonify({"status": True, "settings": get_settings()})


@telegram_bot_bp.route("/settings", methods=["PUT"])
@require_role("admin")
def update_settings(current_user=None):
    data = request.get_json() or {}
    return jsonify(save_settings(data))


@telegram_bot_bp.route("/start", methods=["POST"])
@require_role("admin")
def start(current_user=None):
    result = start_bot()
    return jsonify(result), 200 if result.get("status") else 400


@telegram_bot_bp.route("/stop", methods=["POST"])
@require_role("admin")
def stop(current_user=None):
    return jsonify(stop_bot())


@telegram_bot_bp.route("/restart", methods=["POST"])
@require_role("admin")
def restart(current_user=None):
    result = restart_bot()
    return jsonify(result), 200 if result.get("status") else 400
