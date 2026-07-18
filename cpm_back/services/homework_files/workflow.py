import datetime as dt
import tempfile
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from .storage import HomeworkStorage, StorageNotConfigured, safe_pdf_filename

MOSCOW = ZoneInfo('Europe/Moscow')
ACTIVE_REVIEW_STATES = {'submitted', 'in_review', 'revision_requested'}


class HomeworkWorkflowError(RuntimeError):
    def __init__(self, code, status=400, details=None):
        super().__init__(code)
        self.code, self.status, self.details = code, status, details


def _iso(value):
    return value.isoformat(timespec='milliseconds') + ('Z' if value and value.tzinfo is None else '') if value else None


class HomeworkWorkflow:
    def __init__(self, config):
        self.config = config

    def _submission(self, cursor, homework_id, student_id, create=False, lock=False):
        suffix = ' FOR UPDATE' if lock else ''
        cursor.execute(
            'SELECT * FROM homework_submissions WHERE homework_id=%s AND student_id=%s' + suffix,
            (homework_id, student_id),
        )
        row = cursor.fetchone()
        if not row and create:
            cursor.execute(
                "INSERT INTO homework_submissions (homework_id, student_id, state) VALUES (%s,%s,'none') "
                "ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)", (homework_id, student_id),
            )
            cursor.execute('SELECT * FROM homework_submissions WHERE homework_id=%s AND student_id=%s' + suffix,
                           (homework_id, student_id))
            row = cursor.fetchone()
        return row

    @staticmethod
    def _assert_homework(cursor, homework_id, require_published=False):
        cursor.execute('SELECT id,name,deadline,published FROM homework WHERE id=%s', (homework_id,))
        homework = cursor.fetchone()
        if not homework:
            raise HomeworkWorkflowError('homework_not_found', 404)
        if require_published and not homework.get('published'):
            raise HomeworkWorkflowError('homework_not_published', 403)
        return homework

    @staticmethod
    def _assert_actor(cursor, user, student_id, admin=True):
        role, actor_id = user.get('role'), int(user.get('id'))
        if role == 'student':
            if actor_id != int(student_id):
                raise HomeworkWorkflowError('forbidden', 403)
            return
        if role == 'admin' and admin:
            return
        if role != 'proctor':
            raise HomeworkWorkflowError('forbidden', 403)
        cursor.execute(
            'SELECT 1 FROM proctors p JOIN students s ON s.group_id=p.group_id '
            'WHERE p.id=%s AND s.id=%s', (actor_id, student_id),
        )
        if not cursor.fetchone():
            raise HomeworkWorkflowError('student_not_in_current_group', 403)

    def workspace(self, user, homework_id, student_id=None):
        student_id = int(student_id or user.get('id'))
        conn = get_db_connection()
        try:
            cur = conn.cursor(dictionary=True)
            self._assert_actor(cur, user, student_id)
            hw = self._assert_homework(cur, homework_id, require_published=user.get('role') == 'student')
            sub = self._submission(cur, homework_id, student_id)
            cur.execute('SELECT status,result,date_pass,id FROM homework_sessions WHERE homework_id=%s AND student_id=%s',
                        (homework_id, student_id))
            legacy = cur.fetchone()
            cur.execute('SELECT 1 FROM students s JOIN proctors p ON p.group_id=s.group_id WHERE s.id=%s',(student_id,))
            has_proctor=bool(cur.fetchone())
            public_sub = None
            if sub:
                hidden_draft = user.get('role') != 'student' and sub['state'] in ('none','uploading','processing','draft')
                public_sub = None if hidden_draft else {
                    'id': sub['id'], 'state': sub['state'],
                    'submitted_at_utc': _iso(sub['submitted_at_utc']),
                    'revision_count': sub['revision_count'],
                    'has_draft': bool(sub['draft_file_id']) if user.get('role') == 'student' else False,
                    'has_file': bool(sub['current_file_id']),
                    'file_version': sub['draft_file_id'] if user.get('role') == 'student' and sub['draft_file_id'] else sub['current_file_id'],
                    'reviewer': ({'role': sub['reviewer_role'], 'id': sub['reviewer_id']}
                                 if sub['reviewer_id'] else None),
                }
            return {
                'homework': hw, 'legacy_result': legacy,
                'submission': public_sub or {'state': 'none', 'has_draft': False, 'has_file': False},
                'permissions': {
                    'upload': user.get('role') == 'student' and not (legacy and legacy['status']) and (not sub or sub['state'] in ('none','uploading','processing','draft','revision_requested')),
                    'submit': user.get('role') == 'student' and bool(sub and sub['draft_file_id']) and sub['state'] in ('draft','revision_requested'),
                    'chat': not bool(legacy and legacy['status']) and (user.get('role') != 'student' or has_proctor),
                },
            }
        finally:
            close_db_connection(conn)

    def accept_upload(self, user, homework_id, file_storage, client_upload_id):
        if user.get('role') != 'student':
            raise HomeworkWorkflowError('forbidden', 403)
        try:
            uuid.UUID(str(client_upload_id))
        except (ValueError, TypeError):
            raise HomeworkWorkflowError('invalid_client_upload_id')
        if not file_storage or (file_storage.mimetype or '').lower() != 'application/pdf':
            raise HomeworkWorkflowError('pdf_only')
        with tempfile.NamedTemporaryFile(suffix='.pdf') as source:
            file_storage.save(source.name)
            size = Path(source.name).stat().st_size
            if size <= 0 or size > self.config.HOMEWORK_PDF_MAX_BYTES:
                raise HomeworkWorkflowError('source_too_large', 413)
            with open(source.name, 'rb') as stream:
                if stream.read(5) != b'%PDF-':
                    raise HomeworkWorkflowError('invalid_pdf_signature')
            conn = get_db_connection()
            try:
                cur = conn.cursor(dictionary=True)
                hw = self._assert_homework(cur, homework_id, require_published=True)
                self._assert_actor(cur, user, user['id'])
                sub = self._submission(cur, homework_id, user['id'], create=True, lock=True)
                cur.execute('SELECT status FROM homework_sessions WHERE homework_id=%s AND student_id=%s',
                            (homework_id, user['id']))
                legacy = cur.fetchone()
                if legacy and legacy['status']:
                    raise HomeworkWorkflowError('already_graded', 409)
                if sub['state'] not in ('none','uploading','processing','draft','revision_requested'):
                    raise HomeworkWorkflowError('file_locked_after_submit', 409)
                cur.execute('SELECT * FROM homework_file_jobs WHERE student_id=%s AND client_upload_id=%s',
                            (user['id'], str(client_upload_id)))
                existing = cur.fetchone()
                if existing:
                    conn.rollback()
                    return self._job_json(existing)
                job_id = str(uuid.uuid4())
                staging_key = f'staging/{user["id"]}/{job_id}.pdf'
                try:
                    HomeworkStorage(self.config).upload_file(source.name, staging_key)
                except StorageNotConfigured as exc:
                    raise HomeworkWorkflowError(str(exc), 503)
                cur.execute(
                    'INSERT INTO homework_file_jobs '
                    '(id,client_upload_id,submission_id,homework_id,student_id,status,stage,progress,staging_key,source_size_bytes) '
                    "VALUES (%s,%s,%s,%s,%s,'queued','checking',5,%s,%s)",
                    (job_id, str(client_upload_id), sub['id'], homework_id, user['id'], staging_key, size),
                )
                if sub['state'] not in ('submitted', 'in_review'):
                    cur.execute("UPDATE homework_submissions SET state='processing' WHERE id=%s", (sub['id'],))
                conn.commit()
                return {'id': job_id, 'status': 'queued', 'stage': 'checking', 'progress': 5}
            except Exception:
                conn.rollback()
                raise
            finally:
                close_db_connection(conn)

    @staticmethod
    def _job_json(row):
        return {key: row.get(key) for key in ('id', 'status', 'stage', 'progress', 'error_code', 'attempts', 'manual_attempts')}

    def job(self, user, job_id):
        conn = get_db_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute('SELECT * FROM homework_file_jobs WHERE id=%s', (job_id,))
            row = cur.fetchone()
            if not row:
                raise HomeworkWorkflowError('job_not_found', 404)
            self._assert_actor(cur, user, row['student_id'])
            return self._job_json(row)
        finally:
            close_db_connection(conn)

    def submit(self, user, homework_id):
        if user.get('role') != 'student':
            raise HomeworkWorkflowError('forbidden', 403)
        conn = get_db_connection()
        old_key = None
        try:
            cur = conn.cursor(dictionary=True)
            hw = self._assert_homework(cur, homework_id, require_published=True)
            sub = self._submission(cur, homework_id, user['id'], lock=True)
            if not sub or not sub['draft_file_id']:
                raise HomeworkWorkflowError('draft_not_ready', 409)
            if sub['state'] in ('submitted', 'in_review', 'graded'):
                raise HomeworkWorkflowError('invalid_state', 409)
            if sub['current_file_id']:
                cur.execute('SELECT object_key FROM homework_submission_files WHERE id=%s', (sub['current_file_id'],))
                old = cur.fetchone(); old_key = old and old['object_key']
                cur.execute("UPDATE homework_submission_files SET status='delete_pending' WHERE id=%s", (sub['current_file_id'],))
            now = dt.datetime.utcnow()
            cur.execute("UPDATE homework_submission_files SET status='current' WHERE id=%s", (sub['draft_file_id'],))
            cur.execute(
                "UPDATE homework_submissions SET state='submitted',current_file_id=draft_file_id,draft_file_id=NULL,"
                'submitted_at_utc=%s,reviewer_role=NULL,reviewer_id=NULL WHERE id=%s', (now, sub['id']),
            )
            self._system_event(cur, homework_id, user['id'], 'submission.sent')
            cur.execute(
                "INSERT INTO notifications (recipient_role,recipient_id,kind,homework_id,student_id) "
                "SELECT 'proctor',p.id,'submission_sent',%s,%s FROM students s JOIN proctors p ON p.group_id=s.group_id WHERE s.id=%s",
                (homework_id,user['id'],user['id']),
            )
            cur.execute('SELECT p.id FROM students s JOIN proctors p ON p.group_id=s.group_id WHERE s.id=%s',(user['id'],));proctor=cur.fetchone()
            conn.commit()
            if proctor:
                try:
                    from .push import dispatch_push
                    dispatch_push('proctor',proctor['id'],'Домашняя работа отправлена',hw['name'],'/cabinet/proctor/review-queue')
                except Exception:pass
            if old_key:
                try: HomeworkStorage(self.config).delete(old_key)
                except Exception: pass
            return {'state': 'submitted', 'submitted_at_utc': _iso(now)}
        except Exception:
            conn.rollback(); raise
        finally:
            close_db_connection(conn)

    def review_queue(self, user, state=None, limit=50, after=0):
        if user.get('role') not in ('proctor', 'admin'):
            raise HomeworkWorkflowError('forbidden', 403)
        conn = get_db_connection()
        try:
            cur = conn.cursor(dictionary=True)
            params = [after]
            scope = ''
            if user['role'] == 'proctor':
                scope = ' AND p.id=%s'; params.append(user['id'])
            states = [state] if state else ['submitted', 'in_review', 'revision_requested']
            marks = ','.join(['%s'] * len(states)); params.extend(states); params.append(min(max(int(limit), 1), 100))
            cur.execute(
                'SELECT hs.id,hs.homework_id,hs.student_id,hs.state,hs.submitted_at_utc,hs.reviewer_role,hs.reviewer_id,'
                'h.name homework_name,h.deadline,s.full_name student_name,g.name group_name '
                'FROM homework_submissions hs JOIN homework h ON h.id=hs.homework_id '
                'JOIN students s ON s.id=hs.student_id LEFT JOIN `groups` g ON g.id=s.group_id '
                'LEFT JOIN proctors p ON p.group_id=s.group_id '
                f'WHERE hs.id>%s{scope} AND hs.state IN ({marks}) ORDER BY hs.id LIMIT %s', tuple(params),
            )
            items = cur.fetchall()
            return {'items': items, 'next_cursor': items[-1]['id'] if items else None}
        finally:
            close_db_connection(conn)

    def transition(self, user, submission_id, action, message=None, result=None):
        if user.get('role') not in ('proctor', 'admin'):
            raise HomeworkWorkflowError('forbidden', 403)
        conn = get_db_connection(); delete_key = None
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute('SELECT * FROM homework_submissions WHERE id=%s FOR UPDATE', (submission_id,))
            sub = cur.fetchone()
            if not sub: raise HomeworkWorkflowError('submission_not_found', 404)
            self._assert_actor(cur, user, sub['student_id'])
            if action == 'claim':
                if sub['state'] != 'submitted': raise HomeworkWorkflowError('invalid_state', 409)
                cur.execute("UPDATE homework_submissions SET state='in_review',reviewer_role=%s,reviewer_id=%s WHERE id=%s",
                            (user['role'], user['id'], submission_id)); event='review.claimed'
            elif action == 'takeover':
                if user['role'] != 'admin' or sub['state'] != 'in_review':
                    raise HomeworkWorkflowError('invalid_state', 409)
                cur.execute("UPDATE homework_submissions SET reviewer_role='admin',reviewer_id=%s WHERE id=%s",
                            (user['id'], submission_id)); event='review.claimed'
            elif action == 'release':
                if sub['state'] != 'in_review' or (user['role'] != 'admin' and sub['reviewer_id'] != user['id']):
                    raise HomeworkWorkflowError('not_reviewer', 409)
                cur.execute("UPDATE homework_submissions SET state='submitted',reviewer_role=NULL,reviewer_id=NULL WHERE id=%s", (submission_id,)); event='review.released'
            elif action == 'request-revision':
                if sub['state'] != 'in_review' or not (message or '').strip(): raise HomeworkWorkflowError('revision_message_required', 409)
                if user['role'] != 'admin' and sub['reviewer_id'] != user['id']: raise HomeworkWorkflowError('not_reviewer', 409)
                cur.execute("UPDATE homework_submissions SET state='revision_requested',reviewer_role=NULL,reviewer_id=NULL,revision_count=revision_count+1 WHERE id=%s", (submission_id,)); event='revision.requested'
                self._chat_message(cur, sub['homework_id'], sub['student_id'], user, message.strip())
            elif action == 'grade':
                if sub['state'] != 'in_review': raise HomeworkWorkflowError('invalid_state', 409)
                if user['role'] != 'admin' and sub['reviewer_id'] != user['id']: raise HomeworkWorkflowError('not_reviewer', 409)
                score = self._grade(cur, sub, result); event='submission.graded'
            elif action == 'edit-grade':
                if sub['state'] != 'graded': raise HomeworkWorkflowError('invalid_state', 409)
                score=int(result)
                if score<0 or score>100: raise HomeworkWorkflowError('invalid_result')
                cur.execute('UPDATE homework_sessions SET result=%s WHERE homework_id=%s AND student_id=%s',(score,sub['homework_id'],sub['student_id']))
                cur.execute("INSERT INTO notifications (recipient_role,recipient_id,kind,homework_id,student_id) VALUES ('student',%s,'grade_changed',%s,%s)",(sub['student_id'],sub['homework_id'],sub['student_id']));event='grade.changed'
            elif action == 'resubmit':
                cur.execute('SELECT object_key FROM homework_submission_files WHERE id=%s', (sub['current_file_id'],)); f=cur.fetchone(); delete_key=f and f['object_key']
                cur.execute('DELETE FROM homework_submission_files WHERE submission_id=%s', (submission_id,))
                cur.execute("UPDATE homework_submissions SET state='none',draft_file_id=NULL,current_file_id=NULL,reviewer_role=NULL,reviewer_id=NULL,submitted_at_utc=NULL WHERE id=%s", (submission_id,))
                cur.execute('INSERT INTO homework_sessions (homework_id,student_id,status,result,date_pass) VALUES (%s,%s,0,0,NULL) ON DUPLICATE KEY UPDATE status=0,result=0,date_pass=NULL', (sub['homework_id'], sub['student_id']))
                self._delete_chat(cur, sub['homework_id'], sub['student_id']); event='submission.resubmitted'
            else: raise HomeworkWorkflowError('unknown_action', 404)
            if action not in ('request-revision', 'grade', 'resubmit', 'edit-grade'):
                self._system_event(cur, sub['homework_id'], sub['student_id'], event)
            notice_kind={'claim':'review_claimed','request-revision':'revision_requested','grade':'graded'}.get(action)
            if notice_kind:
                cur.execute("INSERT INTO notifications (recipient_role,recipient_id,kind,homework_id,student_id) VALUES ('student',%s,%s,%s,%s)",(sub['student_id'],notice_kind,sub['homework_id'],sub['student_id']))
            conn.commit()
            if action in ('claim','request-revision','grade','edit-grade'):
                title={'claim':'Работа взята на проверку','request-revision':'Нужна доработка','grade':'Работа оценена','edit-grade':'Балл изменён'}[action]
                try:
                    from .push import dispatch_push
                    dispatch_push('student',sub['student_id'],title,'Откройте домашнюю работу','/cabinet/student/homework')
                except Exception:pass
            if delete_key:
                try: HomeworkStorage(self.config).delete(delete_key)
                except Exception: pass
            return {'status': True, 'state': 'graded' if action in ('grade','edit-grade') else None, 'result': score if action in ('grade','edit-grade') else None}
        except Exception:
            conn.rollback(); raise
        finally: close_db_connection(conn)

    def _grade(self, cur, sub, requested):
        cur.execute('SELECT deadline FROM homework WHERE id=%s', (sub['homework_id'],)); deadline=cur.fetchone()['deadline']
        submitted = sub['submitted_at_utc'].replace(tzinfo=dt.timezone.utc).astimezone(MOSCOW).date()
        suggested = 100 if deadline is None else max(0, 100 - 5 * max(0, (submitted - deadline).days))
        score = suggested if requested is None else int(requested)
        if score < 0 or score > 100: raise HomeworkWorkflowError('invalid_result')
        cur.execute('INSERT INTO homework_sessions (homework_id,student_id,status,result,date_pass) VALUES (%s,%s,1,%s,%s) ON DUPLICATE KEY UPDATE status=1,result=VALUES(result),date_pass=VALUES(date_pass)', (sub['homework_id'],sub['student_id'],score,submitted))
        cur.execute("UPDATE homework_submissions SET state='graded',reviewer_role=NULL,reviewer_id=NULL WHERE id=%s", (sub['id'],))
        cur.execute("UPDATE homework_submission_files SET status='final' WHERE id=%s", (sub['current_file_id'],))
        self._delete_chat(cur, sub['homework_id'], sub['student_id'])
        return score

    @staticmethod
    def _delete_chat(cur, homework_id, student_id):
        cur.execute('SELECT id FROM homework_chat_threads WHERE homework_id=%s AND student_id=%s', (homework_id,student_id)); thread=cur.fetchone()
        if thread:
            cur.execute('DELETE FROM homework_realtime_outbox WHERE thread_id=%s', (thread['id'],))
            cur.execute('DELETE FROM homework_realtime_presence WHERE thread_id=%s', (thread['id'],))
            cur.execute('DELETE FROM homework_chat_threads WHERE id=%s', (thread['id'],))

    @staticmethod
    def _thread(cur, homework_id, student_id):
        cur.execute("INSERT INTO homework_chat_threads (homework_id,student_id) VALUES (%s,%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id),updated_at=CURRENT_TIMESTAMP(6)", (homework_id,student_id))
        cur.execute('SELECT id FROM homework_chat_threads WHERE homework_id=%s AND student_id=%s', (homework_id,student_id)); return cur.fetchone()['id']

    def _system_event(self, cur, homework_id, student_id, code):
        thread_id=self._thread(cur,homework_id,student_id)
        cur.execute("INSERT INTO homework_chat_messages (thread_id,sender_role,kind,event_code) VALUES (%s,'system','system',%s)", (thread_id,code))

    def _chat_message(self, cur, homework_id, student_id, user, body):
        thread_id=self._thread(cur,homework_id,student_id)
        cur.execute("INSERT INTO homework_chat_messages (thread_id,sender_role,sender_id,kind,body) VALUES (%s,%s,%s,'user',%s)", (thread_id,user['role'],user['id'],body[:1000]))

    def file_url(self, user, submission_id, draft=False, download=False):
        conn=get_db_connection()
        try:
            cur=conn.cursor(dictionary=True); cur.execute('SELECT * FROM homework_submissions WHERE id=%s',(submission_id,)); sub=cur.fetchone()
            if not sub: raise HomeworkWorkflowError('submission_not_found',404)
            self._assert_actor(cur,user,sub['student_id'])
            if draft and user['role'] != 'student': raise HomeworkWorkflowError('draft_private',403)
            file_id=sub['draft_file_id'] if draft else sub['current_file_id']
            if not file_id: raise HomeworkWorkflowError('file_not_found',404)
            cur.execute('SELECT object_key FROM homework_submission_files WHERE id=%s',(file_id,)); file=cur.fetchone()
            cur.execute('SELECT s.full_name,h.name FROM students s JOIN homework h ON h.id=%s WHERE s.id=%s',(sub['homework_id'],sub['student_id'])); names=cur.fetchone()
            timestamp=(sub['submitted_at_utc'] or dt.datetime.utcnow()).replace(tzinfo=dt.timezone.utc).astimezone(MOSCOW)
            filename=safe_pdf_filename(names['full_name'],names['name'],timestamp)
            try: url=HomeworkStorage(self.config).presign(file['object_key'],filename,inline=not download)
            except StorageNotConfigured as exc: raise HomeworkWorkflowError(str(exc),503)
            return {'url':url,'expires_in':self.config.S3_PRESIGN_TTL_SECONDS,'filename':filename}
        finally: close_db_connection(conn)
