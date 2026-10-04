"""Persisted resumable coordinator; no distributed rollback is implied.

Each tick claims a lease under the cross-service fence, performs one bounded
stage, commits its checkpoint, and releases the fence. A redeployed process
resumes queued/waiting/expired-running jobs. Partial failure keeps the barrier.
"""
from datetime import timedelta
import hashlib
import logging
import threading
import uuid

from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from cpm_back.db.mongo import get_mongo_db
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from .common import DeletionError, digest, dumps, loads, one, rows, quoted, transaction, utcnow, require_enabled, enabled
from .inventory import collect, COUNT_KEYS, DIRECT, CHILDREN, HOMEWORK_OWNED, ACCOUNT, MONGO, mongo_filter
from .retention import apply_retained_fragments, identity
from .commands import sanitize_commands, verify_sanitized_commands
from .legacy import LEGACY_TABLES, verify_legacy_residual
from .fk_scope import verify_manifest_fk_scope
from .fence import mutation_fence, verify_write_barrier_schema

logger = logging.getLogger(__name__)
CONFIRM_TTL = 300
LEASE_SECONDS = 120


def serializer():
    return URLSafeTimedSerializer(current_app.config['JWT_SECRET_KEY'], salt='student-erasure-v1')


def actor_key(actor):
    return [actor['role'], int(actor['id'])]


def serialize_job(row):
    if not row:
        return None
    total, completed = int(row['total_units']), int(row['completed_units'])
    done = row['status'] == 'completed'
    return {
        'id': row['id'], 'student_id': int(row['student_id']), 'status': row['status'],
        'stage': row['stage'], 'completed_units': completed, 'total_units': total,
        'percent': 100 if done else min(99, int(completed * 100 / max(1, total))),
        'error': row.get('error_code'), 'retryable': row['status'] == 'failed',
        'student': loads(row['student_json']),
        'created_at': row['created_at'].isoformat() + 'Z', 'updated_at': row['updated_at'].isoformat() + 'Z',
    }


def get_job(job_id):
    with transaction() as cursor:
        job = one(cursor, 'SELECT * FROM student_deletion_jobs WHERE id=%s', (job_id,))
    if not job:
        raise DeletionError('job_not_found', 'Задача не найдена.', 404)
    return serialize_job(job)


def list_jobs(student_id):
    with transaction() as cursor:
        return [serialize_job(row) for row in rows(cursor, 'SELECT * FROM student_deletion_jobs WHERE student_id=%s ORDER BY created_at DESC', (student_id,))]


def student_info(cursor, student_id):
    row = one(cursor, 'SELECT s.id,s.full_name AS name,g.name AS group_name FROM students s LEFT JOIN `groups` g ON g.id=s.group_id WHERE s.id=%s', (student_id,))
    if not row:
        raise DeletionError('student_not_found', 'Ученик не найден.', 404)
    return row


def preview(student_id, actor):
    require_enabled()
    # Availability is explicit; a failed dependency never becomes an empty count.
    result = {'student': {'id': student_id, 'name': None, 'group_name': None},
              'available': False, 'blockers': [], 'counts': {key: None for key in COUNT_KEYS},
              'details': [], 'confirmation_token': None, 'expires_at': None,
              'fingerprint': None, 'active_job': None}
    try:
        with mutation_fence(force=True):
            with transaction() as cursor:
                existing = one(cursor, 'SELECT * FROM student_deletion_jobs WHERE student_id=%s', (student_id,))
                if existing:
                    result['active_job'] = serialize_job(existing)
                    result['student'] = loads(existing['student_json'])
                    result['blockers'] = ['Удаление этого ученика уже запущено.']
                    return result
                result['student'] = student_info(cursor, student_id)
                inventory = collect(cursor, get_mongo_db(), student_id)
        result.update(available=True, counts=inventory['counts'], details=inventory['details'], fingerprint=inventory['fingerprint'])
        result['confirmation_token'] = serializer().dumps({'actor': actor_key(actor), 'student_id': student_id, 'fingerprint': inventory['fingerprint']})
        result['expires_at'] = (utcnow() + timedelta(seconds=CONFIRM_TTL)).isoformat() + 'Z'
    except DeletionError as error:
        if error.status == 404:
            raise
        result['blockers'] = [error.message]
    except Exception as error:
        logger.warning('student deletion preview unavailable: %s', type(error).__name__)
        result['blockers'] = ['Не удалось проверить все источники данных. Удаление заблокировано.']
    return result


def validate_confirmation(token, student_id, actor):
    if not isinstance(token, str) or not token or len(token) > 4096:
        raise DeletionError('confirmation_required', 'Сначала подтвердите состав удаления.', 400)
    try:
        payload = serializer().loads(token, max_age=CONFIRM_TTL)
    except SignatureExpired:
        raise DeletionError('confirmation_expired', 'Подтверждение истекло. Обновите расчёт.')
    except BadSignature:
        raise DeletionError('invalid_confirmation', 'Подтверждение недействительно.', 400)
    if payload.get('actor') != actor_key(actor) or payload.get('student_id') != student_id:
        raise DeletionError('invalid_confirmation', 'Подтверждение относится к другому пользователю или ученику.', 403)
    return payload


def start(student_id, actor, token, idempotency_key):
    require_enabled()
    try:
        if str(uuid.UUID(idempotency_key)) != idempotency_key:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise DeletionError('invalid_idempotency_key', 'Требуется UUID запроса.', 400)
    confirmation_hash = hashlib.sha256(str(token).encode()).hexdigest()
    with mutation_fence(force=True):
        with transaction() as cursor:
            prior = one(cursor, 'SELECT * FROM student_deletion_jobs WHERE actor_role=%s AND actor_id=%s AND idempotency_key=%s', (*actor_key(actor), idempotency_key))
            if prior:
                if int(prior['student_id']) != student_id or prior['confirmation_hash'] != confirmation_hash:
                    raise DeletionError('idempotency_conflict', 'Ключ запроса уже использован для другого подтверждения.')
                return serialize_job(prior)
        confirmation = validate_confirmation(token, student_id, actor)
        connection = get_db_connection()
        try:
            # Current reads after the trigger's SQL-writer drain, not a stale RR snapshot.
            connection.start_transaction(isolation_level='READ COMMITTED')
            with transaction(connection) as cursor:
                existing = one(cursor, 'SELECT * FROM student_deletion_jobs WHERE student_id=%s FOR UPDATE', (student_id,))
                if existing:
                    connection.rollback()
                    return serialize_job(existing)
                student = student_info(cursor, student_id)
                job_id = str(uuid.uuid4())
                # Guard trigger drains all already-started SQL personal writes.
                # A changed preview rolls this insert back; no destructive step occurred.
                cursor.execute('INSERT INTO student_deletion_barriers(student_id,job_id,created_at) VALUES (%s,%s,UTC_TIMESTAMP(6))', (student_id, job_id))
                inventory = collect(cursor, get_mongo_db(), student_id)
                if inventory['fingerprint'] != confirmation['fingerprint']:
                    raise DeletionError('preview_changed', 'Данные изменились. Проверьте новый расчёт и подтвердите ещё раз.')
                manifest = inventory['manifest']
                total = (sum(len(keys) for table, keys in manifest['sql'].items() if table not in HOMEWORK_OWNED)
                         + sum(len(keys) for keys in manifest['mongo'].values()) + len(manifest['retained']) + len(manifest.get('command_actions', []))
                         + manifest['homework_units'] + 1)
                cursor.execute('''INSERT INTO student_deletion_jobs
                    (id,student_id,actor_role,actor_id,idempotency_key,confirmation_hash,fingerprint,student_json,manifest_json,total_units,created_at,updated_at,available_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))''',
                    (job_id, student_id, *actor_key(actor), idempotency_key, confirmation_hash, inventory['fingerprint'], dumps(student), dumps(manifest), total))
                cursor.execute('INSERT INTO homework_student_purges(job_id,student_id,created_at,updated_at,available_at) VALUES (%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))', (job_id, student_id))
                # Cancel publication of any stale rating snapshot before it can recreate data.
                cursor.execute("SHOW TABLES LIKE 'rating_source_state'")
                if cursor.fetchone():
                    cursor.execute('UPDATE rating_source_state SET source_revision=source_revision+1 WHERE id=1')
                result = one(cursor, 'SELECT * FROM student_deletion_jobs WHERE id=%s', (job_id,))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            close_db_connection(connection)
    return serialize_job(result)


def retry(job_id):
    require_enabled()
    with mutation_fence(force=True):
        with transaction() as cursor:
            job = one(cursor, 'SELECT * FROM student_deletion_jobs WHERE id=%s FOR UPDATE', (job_id,))
            if not job:
                raise DeletionError('job_not_found', 'Задача не найдена.', 404)
            if job['status'] == 'failed':
                cursor.execute("UPDATE homework_student_purges SET status='queued',available_at=UTC_TIMESTAMP(6),error_code=NULL WHERE job_id=%s AND status='failed'", (job_id,))
                cursor.execute("UPDATE student_deletion_jobs SET updated_at=UTC_TIMESTAMP(6),status='queued',available_at=UTC_TIMESTAMP(6),lease_owner=NULL,lease_expires_at=NULL,error_code=NULL WHERE id=%s", (job_id,))
                job = one(cursor, 'SELECT * FROM student_deletion_jobs WHERE id=%s', (job_id,))
            return serialize_job(job)


def _checkpoint(cursor, job, stage, completed, *, status='queued', delay=0, total=None):
    cursor.execute('''UPDATE student_deletion_jobs SET updated_at=UTC_TIMESTAMP(6),stage=%s,status=%s,completed_units=%s,
        total_units=%s,available_at=%s,lease_owner=NULL,lease_expires_at=NULL,error_code=NULL
        WHERE id=%s AND lease_owner=%s''',
        (stage, status, completed, job['total_units'] if total is None else total, utcnow()+timedelta(seconds=delay), job['id'], job['lease_owner']))
    if cursor.rowcount != 1:
        raise DeletionError('lease_lost', 'Исполнитель задачи изменился.')


def _delete_manifest_rows(cursor, manifest, tables):
    allowed = set(DIRECT) | set(CHILDREN) | ACCOUNT | LEGACY_TABLES
    count = 0
    for table in manifest['order']:
        if table not in tables:
            continue
        if table not in allowed:
            raise DeletionError('invalid_manifest', 'Неизвестная таблица манифеста.', 503)
        for key in manifest['sql'][table]:
            if not key:
                raise DeletionError('invalid_manifest', 'Нет ключа записи.', 503)
            where = ' AND '.join(quoted(column) + '=%s' for column in key)
            cursor.execute(f'DELETE FROM {quoted(table)} WHERE {where}', tuple(key.values()))
            count += 1
    return count


def process_stage(job):
    manifest, student_id = loads(job['manifest_json']), int(job['student_id'])
    completed = int(job['completed_units'])
    if job['stage'] == 'homework':
        with transaction() as cursor:
            purge = one(cursor, 'SELECT * FROM homework_student_purges WHERE job_id=%s', (job['id'],))
            if not purge:
                raise DeletionError('homework_purge_missing', 'Задача файлового сервиса отсутствует.', 503)
            if purge['status'] == 'failed':
                raise DeletionError('homework_purge_failed', 'Не завершено удаление файлов. Можно повторить.')
            # Manifest includes exact stored keys; actual service total is authoritative.
            hw_total = max(int(purge['total_units']), manifest['homework_units'])
            base_total = int(job['total_units']) - manifest['homework_units']
            total = base_total + hw_total
            # Persist the adjusted contribution on every tick so waiting does not inflate totals.
            manifest['homework_units'] = hw_total
            cursor.execute('UPDATE student_deletion_jobs SET updated_at=UTC_TIMESTAMP(6),manifest_json=%s WHERE id=%s AND lease_owner=%s', (dumps(manifest), job['id'], job['lease_owner']))
            if purge['status'] != 'completed':
                _checkpoint(cursor, job, 'homework', int(purge['completed_units']), status='waiting', delay=3, total=total)
                return
            _checkpoint(cursor, job, 'mongo', hw_total, total=total)
        return
    if job['stage'] == 'mongo':
        database = get_mongo_db()
        # Crash after delete-before-checkpoint: same student-scoped delete is harmless.
        for table, field in MONGO.items():
            database[table].delete_many(mongo_filter(student_id, field))
        if any(database[table].count_documents(mongo_filter(student_id, field)) for table, field in MONGO.items()):
            raise DeletionError('mongo_verification_failed', 'Остались записи тестов или рейтинга.')
        with transaction() as cursor:
            _checkpoint(cursor, job, 'sql', completed + sum(len(keys) for keys in manifest['mongo'].values()))
        return
    if job['stage'] == 'sql':
        with transaction() as cursor:
            verify_manifest_fk_scope(cursor, manifest)
            auth = rows(cursor, "SELECT * FROM auth_users WHERE role='student' AND ref_id=%s", (student_id,))
            credentials = rows(cursor, 'SELECT * FROM student_credentials WHERE student_id=%s', (student_id,)) if 'student_credentials' in manifest['sql'] else []
            logins, _ = identity({'auth_users': auth, 'student_credentials': credentials})
            companions = []
            for other in rows(cursor, "SELECT j.student_id,j.manifest_json FROM student_deletion_jobs j JOIN student_deletion_barriers b ON b.job_id=j.id AND b.student_id=j.student_id WHERE j.status<>'completed' AND j.id<>%s", (job['id'],)):
                other_manifest = loads(other['manifest_json'])
                other_auth = rows(cursor, "SELECT * FROM auth_users WHERE role='student' AND ref_id=%s", (other['student_id'],))
                other_credentials = rows(cursor, 'SELECT * FROM student_credentials WHERE student_id=%s', (other['student_id'],)) if 'student_credentials' in other_manifest['sql'] else []
                other_logins, _ = identity({'auth_users': other_auth, 'student_credentials': other_credentials})
                companions.append({'student_id': int(other['student_id']), 'logins': other_logins,
                                   'retained': other_manifest.get('retained', [])})
            apply_retained_fragments(cursor, manifest['retained'], student_id, logins, companions)
            command_count = sanitize_commands(cursor, manifest.get('command_actions', []))
            count = _delete_manifest_rows(cursor, manifest, set(manifest['sql']) - HOMEWORK_OWNED - ACCOUNT)
            _checkpoint(cursor, job, 'verify', completed + count + len(manifest['retained']) + command_count)
        return
    if job['stage'] == 'verify':
        with transaction() as cursor:
            purge = one(cursor, "SELECT status FROM homework_student_purges WHERE job_id=%s", (job['id'],))
            if not purge or purge['status'] != 'completed':
                raise DeletionError('homework_verification_failed', 'Удаление файлов ещё не подтверждено.')
            remaining = collect(cursor, get_mongo_db(), student_id, readiness=False, legacy_ref_context=manifest.get('legacy_ref_context'))
            verify_legacy_residual(cursor, student_id, manifest.get('legacy_ref_context'))
            verify_sanitized_commands(cursor, manifest.get('command_ids', []))
            remaining_sql = {table: values for table, values in remaining['sql'].items() if table not in ACCOUNT | {'homework_object_ownership'} and values}
            if remaining_sql or any(remaining['manifest']['mongo'].values()) or remaining['manifest']['retained'] or remaining['manifest'].get('command_actions'):
                raise DeletionError('residual_personal_data', 'Итоговая проверка обнаружила оставшиеся данные. Требуется проверка схемы.')
            # Recheck every real incoming FK just before final parent deletion.
            verify_manifest_fk_scope(cursor, manifest)
            # Parent/account is last; trigger/FK errors roll back this entire stage.
            count = _delete_manifest_rows(cursor, manifest, ACCOUNT)
            # Verify final identity absence before declaring 100%.
            if one(cursor, 'SELECT id FROM students WHERE id=%s', (student_id,)) or one(cursor, "SELECT ref_id FROM auth_users WHERE role='student' AND ref_id=%s", (student_id,)):
                raise DeletionError('account_verification_failed', 'Учётная запись ещё существует.')
            final_completed = completed + count + 1
            if final_completed != int(job['total_units']):
                raise DeletionError('progress_manifest_mismatch', 'Состав операции требует проверки.')
            # Keep only non-content operational receipt/tombstone after success.
            cursor.execute('''UPDATE student_deletion_jobs SET updated_at=UTC_TIMESTAMP(6),status='completed',stage='completed',
                completed_units=total_units,completed_at=UTC_TIMESTAMP(6),lease_owner=NULL,lease_expires_at=NULL,
                student_json=%s,manifest_json=%s,error_code=NULL WHERE id=%s AND lease_owner=%s''',
                (dumps({'id': student_id, 'name': 'Удалённый ученик', 'group_name': None}), dumps({'verified': True, 'command_ids': manifest.get('command_ids', [])}), job['id'], job['lease_owner']))
            if cursor.rowcount != 1:
                raise DeletionError('lease_lost')
        return
    raise DeletionError('invalid_stage', 'Неизвестный этап задачи.', 503)


def run_once():
    require_enabled()
    with mutation_fence(force=True):
        token = str(uuid.uuid4())
        with transaction() as cursor:
            job = one(cursor, '''SELECT * FROM student_deletion_jobs
                WHERE (status IN ('queued','waiting') AND available_at<=UTC_TIMESTAMP(6))
                   OR (status='running' AND lease_expires_at<UTC_TIMESTAMP(6))
                ORDER BY created_at LIMIT 1 FOR UPDATE''')
            if not job:
                return False
            barrier = one(cursor, 'SELECT job_id FROM student_deletion_barriers WHERE student_id=%s', (job['student_id'],))
            if not barrier or barrier['job_id'] != job['id']:
                raise DeletionError('barrier_missing', 'Защитная блокировка отсутствует.', 503)
            cursor.execute("UPDATE student_deletion_jobs SET updated_at=UTC_TIMESTAMP(6),status='running',lease_owner=%s,lease_expires_at=%s,attempts=attempts+1 WHERE id=%s", (token, utcnow()+timedelta(seconds=LEASE_SECONDS), job['id']))
            job['lease_owner'], job['status'] = token, 'running'
        try:
            process_stage(job)
        except Exception as error:
            code = error.code if isinstance(error, DeletionError) else 'stage_unavailable'
            logger.warning('student deletion stage failed id=%s stage=%s type=%s', job['id'], job['stage'], type(error).__name__)
            with transaction() as cursor:
                cursor.execute("UPDATE student_deletion_jobs SET updated_at=UTC_TIMESTAMP(6),status='failed',error_code=%s,lease_owner=NULL,lease_expires_at=NULL WHERE id=%s AND lease_owner=%s", (code, job['id'], token))
        return True


def start_worker(app):
    if not enabled() or app.config.get('TESTING') or app.config.get('STUDENT_DELETION_WORKER_DISABLED'):
        return None
    stop = threading.Event()
    def loop():
        while not stop.is_set():
            try:
                with app.app_context():
                    ran = run_once()
            except Exception as error:
                logger.warning('student deletion worker unavailable: %s', type(error).__name__)
                ran = False
            stop.wait(0.25 if ran else 3)
    worker = threading.Thread(target=loop, name='student-deletion-worker', daemon=True)
    worker.start()
    app.extensions['student_deletion_worker_stop'] = stop
    return worker
