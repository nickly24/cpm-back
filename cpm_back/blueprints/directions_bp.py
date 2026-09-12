"""
Направления (экзамены): список направлений из MySQL.
"""
from cpm_back.auth import require_auth
from flask import Blueprint, jsonify
from cpm_back.services.exam.get_directions import get_directions

directions_bp = Blueprint('directions', __name__, url_prefix='')


@directions_bp.route('/directions', methods=['GET'])
@require_auth
def list_directions(current_user=None):
    return jsonify(get_directions())
