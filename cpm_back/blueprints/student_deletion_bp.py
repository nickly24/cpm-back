"""Admin-only deletion control plane. Every endpoint requires users/edit."""
from functools import wraps
from flask import Blueprint, jsonify, request
from cpm_back.auth.jwt_auth import get_current_user
from cpm_back.auth.admin_permissions import has_permission
from cpm_back.services.student_deletion import jobs
from cpm_back.services.student_deletion.common import DeletionError

student_deletion_bp = Blueprint('student_deletion', __name__, url_prefix='/api')


def authorized(function):
    @wraps(function)
    def call(*args, **kwargs):
        actor = get_current_user()
        if not actor:
            return jsonify(error='Требуется авторизация.', code='unauthorized'), 401
        if not has_permission(actor, 'users', 'edit'):
            return jsonify(error='Недостаточно прав для удаления ученика.', code='forbidden'), 403
        try:
            response = function(*args, actor=actor, **kwargs)
        except DeletionError as error:
            response = jsonify(error=error.message, code=error.code), error.status
        except Exception:
            # Do not send DB diagnostics, confirmation values or source data.
            response = jsonify(error='Сервис удаления временно недоступен.', code='deletion_unavailable'), 503
        return response
    return call


@student_deletion_bp.after_request
def no_store(response):
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@student_deletion_bp.get('/students/<int:student_id>/deletion-preview')
@authorized
def preview(student_id, actor):
    return jsonify(jobs.preview(student_id, actor))


@student_deletion_bp.route('/students/<int:student_id>/deletion-jobs', methods=['GET', 'POST'])
@authorized
def collection(student_id, actor):
    if request.method == 'GET':
        return jsonify(jobs=jobs.list_jobs(student_id))
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {'confirmation_token', 'idempotency_key'}:
        raise DeletionError('invalid_body', 'Требуются confirmation_token и idempotency_key.', 400)
    return jsonify(job=jobs.start(student_id, actor, data['confirmation_token'], data['idempotency_key'])), 202


@student_deletion_bp.get('/student-deletion-jobs/<uuid:job_id>')
@authorized
def detail(job_id, actor):
    return jsonify(job=jobs.get_job(str(job_id)))


@student_deletion_bp.post('/student-deletion-jobs/<uuid:job_id>/retry')
@authorized
def retry(job_id, actor):
    data = request.get_json(silent=True)
    if data not in (None, {}):
        raise DeletionError('invalid_body', 'Повтор продолжает существующую задачу без изменения состава.', 400)
    return jsonify(job=jobs.retry(str(job_id))), 202
