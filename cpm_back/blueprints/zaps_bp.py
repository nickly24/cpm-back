"""
Запросы на отгул (zap): создать, список по студенту, все, по ID, обработать.
"""
import base64
from flask import Blueprint, request, jsonify
from cpm_back.auth import require_role, require_self_or_role, require_auth
from cpm_back.services.serv import (
    create_zap,
    get_zaps_by_student,
    get_all_zaps,
    get_zap_by_id,
    process_zap,
)

zaps_bp = Blueprint('zaps', __name__, url_prefix='/api')


@zaps_bp.route('/create-zap', methods=['POST'])
@require_self_or_role('student_id', 'admin')
def create(current_user=None):
    data = request.get_json()
    if not data:
        return jsonify({"status": False, "error": "Данные не предоставлены"}), 400
    student_id = data.get('student_id')
    text = data.get('text')
    images_base64 = data.get('images', [])
    if not student_id or not text:
        return jsonify({"status": False, "error": "student_id и text обязательны"}), 400
    try:
        student_id = int(student_id)
    except (ValueError, TypeError):
        return jsonify({"status": False, "error": "student_id должен быть числом"}), 400
    images_data = []
    for img_base64 in images_base64:
        try:
            file_type = 'image/jpeg'
            if ',' in img_base64:
                mime_type = img_base64.split(',')[0].split(':')[1].split(';')[0]
                file_type = mime_type
                img_base64 = img_base64.split(',')[1]
            img_blob = base64.b64decode(img_base64)
            images_data.append({"data": img_blob, "type": file_type})
        except Exception as e:
            return jsonify({"status": False, "error": f"Ошибка обработки файла: {str(e)}"}), 400
    result = create_zap(student_id, text, images_data if images_data else None)
    return jsonify(result), 200 if result.get('status') else 400


@zaps_bp.route('/get-zaps-student', methods=['POST'])
@require_self_or_role('student_id', 'admin')
def by_student(current_user=None):
    data = request.get_json()
    if not data:
        return jsonify({"status": False, "error": "Данные не предоставлены"}), 400
    student_id = data.get('student_id')
    if not student_id:
        return jsonify({"status": False, "error": "student_id обязателен"}), 400
    try:
        student_id = int(student_id)
    except (ValueError, TypeError):
        return jsonify({"status": False, "error": "student_id должен быть числом"}), 400
    result = get_zaps_by_student(student_id)
    return jsonify(result), 200 if result.get('status') else 400


@zaps_bp.route('/get-all-zaps', methods=['GET'])
@require_role('admin')
def all_zaps(current_user=None):
    status = request.args.get('status', None)
    result = get_all_zaps(status)
    return jsonify(result), 200 if result.get('status') else 400


@zaps_bp.route('/get-zap/<zap_id>', methods=['GET'])
@require_auth
def by_id(zap_id, current_user=None):
    """Админ — любой отгул; студент — только свой (zap.student_id == current_user.id)."""
    result = get_zap_by_id(zap_id)
    if not result.get('status'):
        return jsonify(result), 404
    zap = result.get('zap')
    if not zap:
        return jsonify(result), 404
    # Студент может смотреть только свой отгул
    if current_user.get('role') == 'student':
        try:
            if int(zap.get('student_id')) != int(current_user.get('id')):
                return jsonify({"status": False, "error": "Нет доступа к этому запросу"}), 403
        except (TypeError, ValueError):
            return jsonify({"status": False, "error": "Нет доступа"}), 403
    if result.get('images'):
        for img in result['images']:
            if img.get('img'):
                img_base64 = base64.b64encode(img['img']).decode('utf-8')
                file_type = img.get('type', 'image/jpeg')
                img['img_base64'] = f"data:{file_type};base64,{img_base64}"
                img['file_type'] = file_type
                img['img'] = None
    return jsonify(result), 200


@zaps_bp.route('/process-zap', methods=['POST'])
@require_role('admin')
def process(current_user=None):
    data = request.get_json()
    if not data:
        return jsonify({"status": False, "error": "Данные не предоставлены"}), 400
    zap_id = data.get('zap_id')
    status = data.get('status')
    answer = data.get('answer', '')
    dates = data.get('dates', [])
    if not zap_id or not status:
        return jsonify({"status": False, "error": "zap_id и status обязательны"}), 400
    if status not in ('apr', 'dec'):
        return jsonify({"status": False, "error": "status должен быть 'apr' или 'dec'"}), 400
    result = process_zap(zap_id, status, answer, dates if dates else None)
    return jsonify(result), 200 if result.get('status') else 400
