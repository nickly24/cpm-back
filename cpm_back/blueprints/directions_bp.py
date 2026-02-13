"""
Направления (экзамены): список направлений из MySQL.
"""
from flask import Blueprint, jsonify
from cpm_back.services.exam.get_directions import get_directions

directions_bp = Blueprint('directions', __name__, url_prefix='')


@directions_bp.route('/directions', methods=['GET'])
def list_directions():
    return jsonify(get_directions())
