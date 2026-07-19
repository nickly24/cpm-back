import datetime as dt
import logging
import os
import socket
import tempfile
import threading
import time
import uuid
from pathlib import Path

from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from .pdf_pipeline import process_pdf, PdfRejected
from .storage import HomeworkStorage
from .realtime_events import (
    create_notification,
    queue_job_progress,
    queue_submission_changed,
)

logger = logging.getLogger(__name__)
_started = False
_start_lock = threading.Lock()


def start_homework_job_runner(app):
    global _started
    if not app.config.get('HOMEWORK_JOBS_ENABLED', True):
        return
    with _start_lock:
        if _started:
            return
        _started = True
        thread = threading.Thread(target=_loop, args=(app,), daemon=True, name='homework-pdf-runner')
        thread.start()


def _loop(app):
    with app.app_context():
        while True:
            try:
                _heartbeat()
                if not _run_one(app):
                    time.sleep(1.0)
            except Exception as exc:
                # IDs and error type only; never filenames, URLs or PDF contents.
                logger.warning('homework_job_runner error_code=%s', type(exc).__name__)
                time.sleep(2.0)


def _heartbeat():
    connection=get_db_connection()
    try:
        cursor=connection.cursor();cursor.execute("INSERT INTO application_settings (setting_key,setting_value) VALUES ('homework_runner_heartbeat',UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)");connection.commit()
    finally:close_db_connection(connection)


def _run_one(app):
    conn = get_db_connection()
    lock_held = False
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT GET_LOCK('cpm_homework_pdf_runner', 0) AS acquired")
        lock_held = bool(cur.fetchone()['acquired'])
        if not lock_held:
            return False
        cur.execute("SELECT id,object_key,attempts FROM homework_s3_delete_queue WHERE status IN ('queued','retry') AND available_at<=UTC_TIMESTAMP(6) ORDER BY id LIMIT 1 FOR UPDATE")
        deletion=cur.fetchone()
        if deletion:
            cur.execute("UPDATE homework_s3_delete_queue SET status='running',attempts=attempts+1 WHERE id=%s",(deletion['id'],));conn.commit()
            try:
                HomeworkStorage(app.config).delete(deletion['object_key']);cur.execute('DELETE FROM homework_s3_delete_queue WHERE id=%s',(deletion['id'],));conn.commit()
            except Exception as exc:
                status='failed' if deletion['attempts']+1>=10 else 'retry';cur.execute("UPDATE homework_s3_delete_queue SET status=%s,error_code=%s,available_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 1 MINUTE) WHERE id=%s",(status,type(exc).__name__.lower(),deletion['id']));conn.commit()
            return True
        cur.execute(
            "UPDATE homework_file_jobs SET status='queued',lease_owner=NULL,lease_expires_at=NULL "
            "WHERE status='running' AND lease_expires_at<UTC_TIMESTAMP(6)"
        )
        conn.commit()
        cur.execute(
            "SELECT * FROM homework_file_jobs WHERE status IN ('queued','retry') "
            "AND available_at<=UTC_TIMESTAMP(6) ORDER BY created_at LIMIT 1 FOR UPDATE"
        )
        job = cur.fetchone()
        if not job:
            conn.commit()
            return False
        worker = f'{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}'
        cur.execute(
            "UPDATE homework_file_jobs SET status='running',stage='checking',progress=15,attempts=attempts+1,"
            "lease_owner=%s,lease_expires_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 90 SECOND),heartbeat_at=UTC_TIMESTAMP(6) WHERE id=%s",
            (worker, job['id']),
        )
        cur.execute('SELECT id,homework_id,student_id,status,stage,progress,error_code FROM homework_file_jobs WHERE id=%s',(job['id'],))
        running_job=cur.fetchone();queue_job_progress(cur,running_job['student_id'],running_job)
        conn.commit()
        _process(app, job)
        return True
    finally:
        if lock_held:
            release_cursor = None
            try:
                release_cursor = conn.cursor()
                release_cursor.execute("SELECT RELEASE_LOCK('cpm_homework_pdf_runner')")
                # SELECT always produces a result set. Consume it before the
                # pooled connection is reset, otherwise the pool is poisoned
                # with `Unread result found` for subsequent HTTP requests.
                release_cursor.fetchone()
            except Exception:
                pass
            finally:
                if release_cursor is not None:
                    try:
                        release_cursor.close()
                    except Exception:
                        pass
        close_db_connection(conn)


def _process(app, job):
    storage = HomeworkStorage(app.config)
    final_key = f'processed/drafts/{uuid.uuid4()}.pdf'
    old_draft_key = None
    try:
        with tempfile.TemporaryDirectory(prefix='cpm-hw-') as folder:
            source, output = Path(folder) / 'source.pdf', Path(folder) / 'processed.pdf'
            storage.download_file(job['staging_key'], source)
            _progress(job['id'], 'optimization', 45)
            info = process_pdf(source, output, app.config['HOMEWORK_PDF_MAX_BYTES'], app.config['HOMEWORK_PDF_MAX_PAGES'])
            _progress(job['id'], 's3', 75)
            storage.upload_file(output, final_key)
            conn = get_db_connection()
            try:
                cur = conn.cursor(dictionary=True)
                cur.execute('SELECT status FROM homework_file_jobs WHERE id=%s FOR UPDATE',(job['id'],));current_job=cur.fetchone()
                if not current_job or current_job['status']=='cancelled':
                    conn.rollback();storage.delete(final_key);storage.delete(job['staging_key']);return
                cur.execute('SELECT * FROM homework_submissions WHERE id=%s FOR UPDATE', (job['submission_id'],)); sub=cur.fetchone()
                if sub['draft_file_id']:
                    cur.execute('SELECT object_key FROM homework_submission_files WHERE id=%s',(sub['draft_file_id'],)); old=cur.fetchone(); old_draft_key=old and old['object_key']
                    cur.execute('DELETE FROM homework_submission_files WHERE id=%s',(sub['draft_file_id'],))
                cur.execute(
                    "INSERT INTO homework_submission_files (submission_id,object_key,status,size_bytes,page_count,sha256) VALUES (%s,%s,'draft',%s,%s,%s)",
                    (job['submission_id'],final_key,info['size_bytes'],info['page_count'],info['sha256']),
                )
                file_id=cur.lastrowid
                next_state = 'revision_requested' if sub['current_file_id'] else 'draft'
                cur.execute('UPDATE homework_submissions SET draft_file_id=%s,state=%s WHERE id=%s',(file_id,next_state,job['submission_id']))
                cur.execute("UPDATE homework_file_jobs SET status='ready',stage='ready',progress=100,result_file_id=%s,lease_owner=NULL,lease_expires_at=NULL,error_code=NULL WHERE id=%s",(file_id,job['id']))
                cur.execute('SELECT id,homework_id,student_id,status,stage,progress,error_code FROM homework_file_jobs WHERE id=%s',(job['id'],))
                ready_job=cur.fetchone();queue_job_progress(cur,ready_job['student_id'],ready_job)
                queue_submission_changed(cur,ready_job['student_id'],ready_job['homework_id'],job['submission_id'])
                conn.commit()
            except Exception:
                conn.rollback(); raise
            finally: close_db_connection(conn)
        storage.delete(job['staging_key'])
        if old_draft_key: storage.delete(old_draft_key)
    except PdfRejected as exc:
        _fail(job, exc.code, terminal=True)
    except Exception as exc:
        try: storage.delete(final_key)
        except Exception: pass
        _fail(job, type(exc).__name__.lower(), terminal=int(job['attempts'] or 0) + 1 >= 3)


def _progress(job_id, stage, progress):
    conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True);cur.execute('UPDATE homework_file_jobs SET stage=%s,progress=%s,heartbeat_at=UTC_TIMESTAMP(6),lease_expires_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 90 SECOND) WHERE id=%s',(stage,progress,job_id))
        cur.execute('SELECT id,homework_id,student_id,status,stage,progress,error_code FROM homework_file_jobs WHERE id=%s',(job_id,));job=cur.fetchone()
        if job:queue_job_progress(cur,job['student_id'],job)
        conn.commit()
    finally: close_db_connection(conn)


def _fail(job, code, terminal):
    conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True)
        status='failed' if terminal else 'retry'
        delay=min(30, 2 ** (int(job['attempts'] or 0) + 1))
        cur.execute(
            "UPDATE homework_file_jobs SET status=%s,stage='error',error_code=%s,lease_owner=NULL,lease_expires_at=NULL,available_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND) WHERE id=%s",
            (status,code,delay,job['id']),
        )
        if terminal:
            cur.execute("UPDATE homework_submissions SET state=IF(current_file_id IS NULL,'none',state) WHERE id=%s",(job['submission_id'],))
            create_notification(cur,'student',job['student_id'],'job_failed',job['homework_id'],job['student_id'])
            queue_submission_changed(cur,job['student_id'],job['homework_id'],job['submission_id'])
        cur.execute('SELECT id,homework_id,student_id,status,stage,progress,error_code FROM homework_file_jobs WHERE id=%s',(job['id'],));failed_job=cur.fetchone()
        if failed_job:queue_job_progress(cur,failed_job['student_id'],failed_job)
        conn.commit()
        if terminal:
            try:
                from .push import dispatch_push
                dispatch_push('student',job['student_id'],'Ошибка обработки PDF','Откройте домашнюю работу и повторите загрузку','/cabinet/student/homework')
            except Exception:pass
    finally: close_db_connection(conn)
