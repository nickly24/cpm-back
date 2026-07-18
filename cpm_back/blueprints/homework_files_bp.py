from flask import Blueprint, current_app, jsonify, request

from cpm_back.auth import require_auth, require_role
from cpm_back.config import config
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from cpm_back.services.homework_files import HomeworkWorkflow, HomeworkWorkflowError
from cpm_back.services.homework_files.storage import HomeworkStorage, StorageNotConfigured

homework_files_bp = Blueprint('homework_files', __name__, url_prefix='/api/homework-files')


def _service():
    return HomeworkWorkflow(config)


@homework_files_bp.errorhandler(HomeworkWorkflowError)
def _workflow_error(exc):
    payload = {'status': False, 'error': exc.code}
    if exc.details is not None: payload['details'] = exc.details
    return jsonify(payload), exc.status


@homework_files_bp.get('/workspace/<int:homework_id>')
@require_auth
def workspace(homework_id, current_user=None):
    student_id = request.args.get('student_id', type=int)
    return jsonify(_service().workspace(current_user, homework_id, student_id))


@homework_files_bp.post('/workspace/<int:homework_id>/upload')
@require_role('student')
def upload(homework_id, current_user=None):
    client_id = request.form.get('client_upload_id') or request.headers.get('Idempotency-Key')
    if not client_id: raise HomeworkWorkflowError('client_upload_id_required')
    result = _service().accept_upload(current_user, homework_id, request.files.get('file'), client_id)
    return jsonify(result), 202


@homework_files_bp.post('/workspace/<int:homework_id>/submit')
@require_role('student')
def submit(homework_id, current_user=None):
    return jsonify(_service().submit(current_user, homework_id))


@homework_files_bp.get('/jobs/<job_id>')
@require_auth
def job_status(job_id, current_user=None):
    return jsonify(_service().job(current_user, job_id))


@homework_files_bp.get('/jobs')
@require_role('student')
def active_jobs(current_user=None):
    conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True)
        cur.execute("SELECT id,status,stage,progress,error_code,homework_id,created_at FROM homework_file_jobs WHERE student_id=%s AND status IN ('queued','running','retry','ready','failed') ORDER BY created_at DESC LIMIT 30",(current_user['id'],))
        return jsonify({'items':cur.fetchall()})
    finally: close_db_connection(conn)


@homework_files_bp.post('/jobs/<job_id>/cancel')
@require_role('student')
def cancel_job(job_id, current_user=None):
    conn=get_db_connection(); key=None
    try:
        cur=conn.cursor(dictionary=True); cur.execute('SELECT * FROM homework_file_jobs WHERE id=%s AND student_id=%s FOR UPDATE',(job_id,current_user['id'])); job=cur.fetchone()
        if not job: raise HomeworkWorkflowError('job_not_found',404)
        if job['status'] in ('ready','failed','cancelled'): raise HomeworkWorkflowError('job_not_cancellable',409)
        key=job['staging_key']; cur.execute("UPDATE homework_file_jobs SET status='cancelled',stage='cancelled',lease_expires_at=UTC_TIMESTAMP(6) WHERE id=%s",(job_id,)); conn.commit()
    except Exception: conn.rollback(); raise
    finally: close_db_connection(conn)
    try: HomeworkStorage(config).delete(key)
    except Exception: pass
    return jsonify({'status':True})


@homework_files_bp.post('/jobs/<job_id>/retry')
@require_auth
def retry_job(job_id, current_user=None):
    conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True); cur.execute('SELECT * FROM homework_file_jobs WHERE id=%s FOR UPDATE',(job_id,)); job=cur.fetchone()
        if not job: raise HomeworkWorkflowError('job_not_found',404)
        _service()._assert_actor(cur,current_user,job['student_id'])
        if job['status'] != 'failed': raise HomeworkWorkflowError('job_not_retryable',409)
        if job['manual_attempts'] >= 3: raise HomeworkWorkflowError('manual_retry_limit',409)
        cur.execute("UPDATE homework_file_jobs SET status='queued',stage='checking',progress=5,error_code=NULL,manual_attempts=manual_attempts+1,available_at=UTC_TIMESTAMP(6) WHERE id=%s",(job_id,)); conn.commit()
        return jsonify({'status':True})
    except Exception: conn.rollback(); raise
    finally: close_db_connection(conn)


@homework_files_bp.get('/review-queue')
@require_role('proctor','admin')
def review_queue(current_user=None):
    return jsonify(_service().review_queue(current_user,request.args.get('state'),request.args.get('limit',50,type=int),request.args.get('after',0,type=int)))


def _transition(action):
    @homework_files_bp.post(f'/submissions/<int:submission_id>/{action}', endpoint=f'submission_{action}')
    @require_role('proctor','admin')
    def handler(submission_id, current_user=None):
        body=request.get_json(silent=True) or {}
        return jsonify(_service().transition(current_user,submission_id,action,body.get('message'),body.get('result')))
    return handler


for _action in ('claim','takeover','release','request-revision','grade','edit-grade','resubmit'):
    _transition(_action)


@homework_files_bp.get('/submissions/<int:submission_id>/file-url')
@require_auth
def file_url(submission_id,current_user=None):
    return jsonify(_service().file_url(current_user,submission_id,request.args.get('draft')=='1',request.args.get('download')=='1'))


@homework_files_bp.get('/monitoring')
@require_role('admin')
def monitoring(current_user=None):
    conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True)
        cur.execute('SELECT status,COUNT(*) count FROM homework_file_jobs GROUP BY status'); jobs=cur.fetchall()
        cur.execute("SELECT id,status,stage,progress,error_code,attempts,manual_attempts,created_at FROM homework_file_jobs ORDER BY created_at DESC LIMIT 50");recent_jobs=cur.fetchall()
        cur.execute("SELECT setting_value heartbeat,TIMESTAMPDIFF(SECOND,CAST(setting_value AS DATETIME(6)),UTC_TIMESTAMP(6)) age_seconds FROM application_settings WHERE setting_key='homework_runner_heartbeat'"); runner=cur.fetchone() or {'heartbeat':None,'age_seconds':None}
        try: storage=HomeworkStorage(config).size_summary()
        except (StorageNotConfigured,Exception): storage={'file_count':None,'total_bytes':None}
        failed=next((row['count'] for row in jobs if row['status']=='failed'),0);warnings=[]
        if runner['age_seconds'] is None or runner['age_seconds']>10:warnings.append('runner_unavailable')
        if failed>=5:warnings.append('failed_jobs_growth')
        return jsonify({'jobs':jobs,'recent_jobs':recent_jobs,'runner_heartbeat':runner['heartbeat'],'storage':storage,'warnings':warnings})
    finally: close_db_connection(conn)


@homework_files_bp.get('/archive')
@require_role('admin')
def archive(current_user=None):
    connection=get_db_connection()
    try:
        cursor=connection.cursor(dictionary=True);where=["sub.state='graded'","sub.current_file_id IS NOT NULL"];params=[]
        for key,column in (('student_id','sub.student_id'),('homework_id','sub.homework_id'),('group_id','s.group_id')):
            value=request.args.get(key,type=int)
            if value:where.append(f'{column}=%s');params.append(value)
        if request.args.get('date_from'):where.append('sub.submitted_at_utc>=%s');params.append(request.args['date_from'])
        if request.args.get('date_to'):where.append('sub.submitted_at_utc<DATE_ADD(%s,INTERVAL 1 DAY)');params.append(request.args['date_to'])
        cursor.execute('SELECT sub.id,sub.homework_id,sub.student_id,sub.submitted_at_utc,f.size_bytes,f.page_count,s.full_name student_name,h.name homework_name,g.name group_name FROM homework_submissions sub JOIN homework_submission_files f ON f.id=sub.current_file_id JOIN students s ON s.id=sub.student_id JOIN homework h ON h.id=sub.homework_id LEFT JOIN `groups` g ON g.id=s.group_id WHERE '+' AND '.join(where)+' ORDER BY sub.submitted_at_utc DESC LIMIT 200',tuple(params))
        return jsonify({'items':cursor.fetchall()})
    finally:close_db_connection(connection)
