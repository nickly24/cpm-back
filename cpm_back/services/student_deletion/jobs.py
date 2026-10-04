"""Small Mongo-backed queue: one SQL transaction, then external cleanup.

No SQL schema installation, triggers or global request guards. Mongo's built-in
_id uniqueness and atomic claims keep the queue shared between web processes.
"""
from datetime import timedelta
import hashlib
import logging
import threading
import uuid

from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from cpm_back.db.mongo import get_mongo_db
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from .common import DeletionError, digest, one, rows, quoted, transaction, utcnow
from .inventory import collect, COUNT_KEYS, MONGO, QUEUE_COLLECTION, collect_mongo_documents, MONGO_OWNER_FIELDS, schema, selectors, CHILDREN
from .retention import apply_retained_fragments, collect_retained_fragments, identity
from .commands import sanitize_commands, collect_command_sanitizations

logger = logging.getLogger(__name__)
CONFIRM_TTL = 300
LEASE_SECONDS = 120
FILE_WAIT_SECONDS = 600
_started = False
_start_lock = threading.Lock()


def queue():
    return get_mongo_db()[QUEUE_COLLECTION]


def serializer():
    return URLSafeTimedSerializer(current_app.config['JWT_SECRET_KEY'], salt='student-deletion-simple-v1')


def actor_key(actor):
    return [actor['role'], int(actor['id'])]


def serialize_job(job):
    if not job:
        return None
    total = int(job.get('total_units', 0))
    completed = int(job.get('completed_units', 0))
    return {key: job.get(key) for key in ('id', 'student_id', 'student', 'status', 'stage',
            'counts', 'details', 'warnings', 'residuals', 'student_deleted', 'error', 'retryable')} | {
        'total_units': total, 'completed_units': completed,
        'percent': 100 if job['status'] == 'completed' else min(99, int(completed*100/max(1, total))),
        'created_at': job['created_at'].isoformat()+'Z',
        'updated_at': job['updated_at'].isoformat()+'Z',
    }


def get_job(job_id):
    job = queue().find_one({'id': job_id})
    if not job:
        raise DeletionError('job_not_found', 'Операция не найдена.', 404)
    return serialize_job(job)


def list_jobs(student_id):
    return [serialize_job(job) for job in queue().find({'student_id': student_id}).sort('created_at', -1)]


def student_info(cursor, student_id):
    row = one(cursor, 'SELECT s.id,s.full_name AS name,g.name AS group_name FROM students s '
              'LEFT JOIN `groups` g ON g.id=s.group_id WHERE s.id=%s', (student_id,))
    if not row:
        raise DeletionError('student_not_found', 'Ученик не найден.', 404)
    return row


def preview(student_id, actor):
    result = {'student': {'id': student_id, 'name': None, 'group_name': None},
              'available': False, 'blockers': [], 'warnings': [],
              'counts': {key: None for key in COUNT_KEYS}, 'details': [],
              'confirmation_token': None, 'expires_at': None, 'fingerprint': None,
              'active_job': None}
    try:
        existing = queue().find_one({'_id': str(student_id)})
        if existing:
            result.update(active_job=serialize_job(existing), student=existing['student'])
            return result
        with transaction() as cursor:
            result['student'] = student_info(cursor, student_id)
            inventory = collect(cursor, get_mongo_db(), student_id)
        result.update(available=True, counts=inventory['counts'], details=inventory['details'],
                      warnings=inventory['warnings'], fingerprint=inventory['fingerprint'])
        result['confirmation_token'] = serializer().dumps({'actor': actor_key(actor),
            'student_id': student_id, 'fingerprint': inventory['fingerprint']})
        result['expires_at'] = (utcnow()+timedelta(seconds=CONFIRM_TTL)).isoformat()+'Z'
    except DeletionError as error:
        if error.status == 404:
            raise
        result['blockers'] = [error.message]
    except Exception as error:
        logger.warning('student_deletion_preview_failed error_type=%s', type(error).__name__)
        result['blockers'] = ['Не удалось подсчитать связанные данные. Попробуйте обновить расчёт.']
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
        raise DeletionError('invalid_confirmation', 'Подтверждение относится к другому ученику или пользователю.', 403)
    return payload


def start(student_id, actor, token, idempotency_key):
    try:
        if str(uuid.UUID(idempotency_key)) != idempotency_key:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise DeletionError('invalid_idempotency_key', 'Требуется UUID запроса.', 400)
    confirmation_hash = hashlib.sha256(str(token).encode()).hexdigest()
    existing = queue().find_one({'_id': str(student_id)})
    if existing:
        if existing['actor'] == actor_key(actor) and existing['idempotency_key'] == idempotency_key:
            if existing['confirmation_hash'] != confirmation_hash:
                raise DeletionError('idempotency_conflict', 'Запрос уже принят с другим подтверждением.')
        return serialize_job(existing)
    confirmation = validate_confirmation(token, student_id, actor)
    with transaction() as cursor:
        student = student_info(cursor, student_id)
        inventory = collect(cursor, get_mongo_db(), student_id)
    if inventory['fingerprint'] != confirmation['fingerprint']:
        raise DeletionError('preview_changed', 'Данные изменились. Обновите расчёт и подтвердите удаление ещё раз.')
    now = utcnow()
    job = {'_id': str(student_id), 'id': str(uuid.uuid4()), 'student_id': student_id,
           'student': student, 'actor': actor_key(actor), 'idempotency_key': idempotency_key,
           'confirmation_hash': confirmation_hash, 'status': 'queued', 'stage': 'sql',
           'counts': inventory['counts'], 'details': inventory['details'],
           'warnings': inventory['warnings'], 'residuals': [], 'student_deleted': False,
           'error': None, 'retryable': False, 'completed_units': 0, 'total_units': 1,
           'created_at': now, 'updated_at': now, 'available_at': now}
    try:
        queue().insert_one(job)
    except DuplicateKeyError:
        # Another tab may have submitted the same pupil concurrently.
        job = queue().find_one({'_id': str(student_id)})
    return serialize_job(job)


def retry(job_id):
    job = queue().find_one({'id': job_id})
    if not job:
        raise DeletionError('job_not_found', 'Операция не найдена.', 404)
    if job['status'] not in {'failed', 'partial'}:
        return serialize_job(job)
    # A committed SQL deletion is not undone. Retry cleans the remaining stores.
    unqueued = job.get('unqueued_file_keys', [])
    file_keys = sorted({issue['target'] for issue in job.get('residuals', []) if issue['source'] == 'files'})
    if job.get('student_deleted') and file_keys:
        with transaction() as cursor:
            unqueued = enqueue_files(cursor, {**job, 'file_keys': file_keys})
    stage = 'mongo' if job.get('student_deleted') else 'sql'
    job = queue().find_one_and_update({'id': job_id, 'status': job['status']},
        {'$set': {'status': 'queued', 'stage': stage, 'available_at': utcnow(),
                  'updated_at': utcnow(), 'error': None, 'retryable': False,
                  'residuals': [], 'unqueued_file_keys': unqueued,
                  'file_wait_started_at': utcnow()}}, return_document=ReturnDocument.AFTER)
    return serialize_job(job or queue().find_one({'id': job_id}))


def checkpoint(job, **changes):
    changes['updated_at'] = utcnow()
    result = queue().update_one({'id': job['id'], 'lease_owner': job['lease_owner']}, {'$set': changes})
    if result.matched_count != 1:
        raise DeletionError('lease_lost', 'Операцию продолжает другой обработчик.')
    job.update(changes)


def delete_rows(cursor, manifest):
    count = 0
    for table in manifest['order']:
        keys = manifest['sql'][table]
        if not keys:
            continue
        fields = list(keys[0])
        if not fields or any(set(key) != set(fields) for key in keys):
            raise DeletionError('invalid_manifest', 'Не удалось определить ключи связанных записей.', 500)
        for offset in range(0, len(keys), 200):
            batch = keys[offset:offset+200]
            where = ' OR '.join('('+' AND '.join(quoted(field)+'=%s' for field in fields)+')' for _ in batch)
            cursor.execute('DELETE FROM '+quoted(table)+' WHERE '+where,
                           tuple(key[field] for key in batch for field in fields))
            count += max(0, cursor.rowcount)
    return count


def enqueue_files(cursor, manifest):
    if not manifest['file_queue_available']:
        return list(manifest['file_keys'])
    unqueued = []
    for key in manifest['file_keys']:
        existing = one(cursor, 'SELECT object_key FROM homework_s3_delete_queue WHERE object_key=%s FOR UPDATE', (key,))
        if existing and existing['object_key'] != key:
            # The existing SQL queue uses a case-insensitive UNIQUE key, while
            # S3 keys are case-sensitive. Never treat its other key as this one.
            unqueued.append(key)
            continue
        cursor.execute("INSERT INTO homework_s3_delete_queue(object_key,status,available_at) "
            "VALUES (%s,'queued',UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE "
            "available_at=IF(status='running',available_at,UTC_TIMESTAMP(6)),"
            "attempts=IF(status='running',attempts,0),error_code=IF(status='running',error_code,NULL),"
            "status=IF(status='running',status,'queued')", (key,))
        stored = one(cursor, 'SELECT object_key FROM homework_s3_delete_queue WHERE object_key=%s', (key,))
        if not stored or stored['object_key'] != key:
            unqueued.append(key)
    return unqueued


def sql_stage(job):
    connection = get_db_connection()
    try:
        connection.start_transaction(isolation_level='READ COMMITTED')
        with transaction(connection) as cursor:
            exists = one(cursor, 'SELECT id FROM students WHERE id=%s FOR UPDATE', (job['student_id'],))
            if not exists:
                if not job.get('sql_started'):
                    raise DeletionError('student_not_found', 'Ученик уже отсутствует. Проверьте оставшиеся данные.', 404)
                # A worker died after COMMIT but before updating the Mongo receipt.
                checkpoint(job, student_deleted=True, stage='mongo',
                           completed_units=job['sql_units'])
                connection.rollback()
                return
            inventory = collect(cursor, get_mongo_db(), job['student_id'])
            manifest = inventory['manifest']
            sql_units = sum(len(keys) for keys in manifest['sql'].values()) + len(manifest['retained']) + len(manifest['command_actions'])
            mongo_units = sum(len(values) for values in manifest['mongo'].values())
            # Remember exact keys before SQL can remove their only metadata.
            checkpoint(job, sql_started=True, sql_units=sql_units, mongo_units=mongo_units, mongo_counts={table: len(values) for table, values in manifest['mongo'].items()},
                       total_units=sql_units+mongo_units+len(manifest['file_keys'])+1,
                       file_keys=manifest['file_keys'], unqueued_file_keys=[], file_queue_available=manifest['file_queue_available'],
                       counts=inventory['counts'], details=inventory['details'], legacy_context=manifest['legacy_ref_context'],
                       warnings=inventory['warnings'], sql_manifest=manifest['sql'])
            logins, _ = identity(inventory['sql'])
            apply_retained_fragments(cursor, manifest['retained'], job['student_id'], logins)
            sanitize_commands(cursor, manifest['command_actions'])
            delete_rows(cursor, manifest)
            checkpoint(job, unqueued_file_keys=enqueue_files(cursor, manifest))
            cursor.execute("SHOW TABLES LIKE 'rating_source_state'")
            if cursor.fetchone():
                cursor.execute('UPDATE rating_source_state SET source_revision=source_revision+1 WHERE id=1')
        connection.commit()
        checkpoint(job, student_deleted=True, stage='mongo', completed_units=sql_units)
    except Exception:
        connection.rollback()
        raise
    finally:
        close_db_connection(connection)


def mongo_stage(job):
    residuals = []
    completed = 0
    db = get_mongo_db()
    for table in MONGO:
        try:
            documents, _ = collect_mongo_documents(db, table, job['student_id'])
            for offset in range(0, len(documents), 200):
                # Recheck the owners in the DELETE predicate, including aliases.
                # A document transferred to somebody else after the read survives.
                batch = documents[offset:offset+200]
                predicates = [{'_id': document['_id'],
                               **{field: document.get(field) for field in MONGO_OWNER_FIELDS}}
                              for document in batch]
                db[table].delete_many({'$or': predicates})
            remaining, ambiguous = collect_mongo_documents(db, table, job['student_id'])
            expected = job.get('mongo_counts', {}).get(table, 0)
            completed += max(0, expected-len(remaining))
            residuals.extend(ambiguous)
            if remaining:
                residuals.append({'source': 'mongo', 'target': table, 'count': len(remaining),
                                  'message': 'Остались документы ученика. Удалите их вручную или повторите очистку.'})
        except Exception as error:
            logger.warning('student_deletion_mongo_failed collection=%s error_type=%s', table, type(error).__name__)
            residuals.append({'source': 'mongo', 'target': table,
                              'message': 'Не удалось удалить документы ученика. Повторите очистку или проверьте коллекцию вручную.'})
    checkpoint(job, residuals=residuals, stage='files', mongo_completed_units=completed,
               completed_units=job['sql_units']+completed, file_wait_started_at=utcnow())


def files_stage(job):
    keys = job.get('file_keys', [])
    pending, failed = [], list(job.get('unqueued_file_keys', []))
    if not job.get('file_queue_available'):
        failed = list(keys)
    elif keys:
        with transaction() as cursor:
            for offset in range(0, len(keys), 200):
                batch = [key for key in keys[offset:offset+200] if key not in failed]
                if not batch:
                    continue
                queue_rows = rows(cursor, 'SELECT object_key,status,error_code FROM homework_s3_delete_queue '
                                  'WHERE object_key IN ('+','.join(['%s']*len(batch))+')', batch)
                pending.extend(row['object_key'] for row in queue_rows if row['object_key'] in batch and row['status'] in {'queued', 'running', 'retry'})
                failed.extend(row['object_key'] for row in queue_rows if row['object_key'] in batch and row['status'] not in {'queued', 'running', 'retry'})
    elapsed = (utcnow()-job.get('file_wait_started_at', utcnow())).total_seconds()
    completed = job['sql_units']+job.get('mongo_completed_units', 0)+len(keys)-len(pending)-len(failed)
    if pending and elapsed < FILE_WAIT_SECONDS:
        checkpoint(job, status='waiting', available_at=utcnow()+timedelta(seconds=2), completed_units=completed)
        return
    residuals = [*job['residuals'], *({'source': 'files', 'target': key,
        'message': 'Файл не удалён или сервис файлов не подтвердил удаление. Повторите очистку либо удалите ключ вручную.'}
        for key in [*failed, *pending])]
    checkpoint(job, residuals=residuals, stage='verify', completed_units=completed)


def remaining_sql_records(cursor, table, pks, selected, manifest):
    """Check owner selectors and saved IDs in bounded queries, without double counts."""
    scopes = [selected[table]] if table in selected else []
    keys = manifest.get(table, [])
    for offset in range(0, len(keys), 200):
        batch = keys[offset:offset+200]
        where = ' OR '.join('('+' AND '.join(quoted(field)+'=%s' for field in key)+')' for key in batch)
        scopes.append((where, [value for key in batch for value in key.values()]))
    if table in CHILDREN:
        field, parent, parent_key = CHILDREN[table]
        parent_ids = [key[parent_key] for key in manifest.get(parent, []) if parent_key in key]
        for offset in range(0, len(parent_ids), 200):
            batch = parent_ids[offset:offset+200]
            scopes.append((quoted(field)+' IN ('+','.join(['%s']*len(batch))+')', batch))
    found = set()
    if not pks:
        return sum(int(one(cursor, 'SELECT COUNT(*) count FROM '+quoted(table)+' WHERE '+where, args)['count'])
                   for where, args in scopes)
    fields = ','.join(map(quoted, pks))
    for where, args in scopes:
        found.update(digest(row) for row in rows(cursor, 'SELECT '+fields+' FROM '+quoted(table)+' WHERE '+where, args))
    return len(found)


def finish_stage(job):
    residuals = list(job['residuals'])
    warnings = list(job['warnings'])
    try:
        with transaction() as cursor:
            columns, pks, _ = schema(cursor)
            selected = selectors(columns, job['student_id'])
            # Include new direct rows and orphan children as well as saved keys.
            for table in set(selected) | set(job.get('sql_manifest', {})):
                if table not in columns:
                    continue
                count = remaining_sql_records(cursor, table, pks[table], selected, job.get('sql_manifest', {}))
                if count:
                    residuals.append({'source': 'mysql', 'target': table, 'count': count,
                                      'message': 'Остались связанные записи. Проверьте таблицу вручную.'})
            if job.get('legacy_context'):
                from .legacy import collect_legacy
                legacy = collect_legacy(cursor, columns, job['student_id'], context=job['legacy_context'], strict=False)
                warnings.extend(issue for issue in legacy['warnings'] if issue not in warnings)
                for table, values in legacy['sql'].items():
                    if values and not any(r['source']=='mysql' and r['target']==table for r in residuals):
                        residuals.append({'source': 'mysql', 'target': table, 'count': len(values),
                                          'message': 'Остались старые связанные записи. Проверьте их вручную.'})
            # Shared import JSON and command receipts can be written without FKs.
            # Detect late personal data; do not start a second destructive SQL pass.
            identity_scope = {'students': [{'full_name': job['student'].get('name')}]}
            for target, inspect in (
                ('import_snapshots', lambda: collect_retained_fragments(cursor, columns, pks, job['student_id'], identity_scope)),
                ('exam_admin_commands', lambda: collect_command_sanitizations(cursor, columns, job['student_id'], identity_scope)),
            ):
                try:
                    result = inspect()
                    if target == 'import_snapshots':
                        affected = {}
                        for entry in result['manifest']:
                            affected.setdefault(entry['table'], set()).add(digest(entry['key']))
                        counts = {table: len(keys) for table, keys in affected.items()}
                    else:
                        counts = {target: len(result['actions'])}
                    for table, count in counts.items():
                        if count and not any(r['source']=='mysql' and r['target']==table for r in residuals):
                            residuals.append({'source': 'mysql', 'target': table, 'count': count,
                                              'message': 'Остались персональные данные в общих записях. Проверьте их вручную.'})
                except DeletionError as error:
                    issue = {'source': 'mysql', 'target': target, 'message': error.message}
                    if issue not in warnings:
                        warnings.append(issue)
    except Exception:
        residuals.append({'source': 'mysql', 'target': 'verification',
                          'message': 'Не удалось проверить остатки записей после удаления. Нужна ручная проверка базы.'})
    for table in MONGO:
        try:
            remaining, ambiguous = collect_mongo_documents(get_mongo_db(), table, job['student_id'])
            residuals.extend(issue for issue in ambiguous if issue not in residuals)
            if remaining and not any(r['source']=='mongo' and r['target']==table for r in residuals):
                residuals.append({'source': 'mongo', 'target': table, 'count': len(remaining),
                                  'message': 'После очистки появились или остались документы ученика.'})
        except Exception:
            if not any(r['source']=='mongo' and r['target']==table for r in residuals):
                residuals.append({'source': 'mongo', 'target': table,
                                  'message': 'Не удалось проверить остатки документов.'})
    partial = bool(residuals or warnings)
    changes = {'status': 'partial' if partial else 'completed', 'stage': 'completed',
               'residuals': residuals, 'warnings': warnings, 'retryable': bool(residuals),
               'error': 'Часть данных требует дополнительной очистки.' if partial else None,
               'completed_units': job['completed_units']+1}
    if not partial:
        changes['completed_units'] = job['total_units']
        changes.update(file_keys=[], unqueued_file_keys=[], sql_manifest={}, legacy_context={},
                       student={'id': job['student_id'], 'name': 'Удалённый ученик', 'group_name': None})
    checkpoint(job, **changes)


def run_once():
    now = utcnow()
    job = queue().find_one_and_update({'$or': [
        {'status': {'$in': ['queued', 'waiting']}, 'available_at': {'$lte': now}},
        {'status': 'running', 'lease_expires_at': {'$lt': now}},
    ]}, {'$set': {'status': 'running', 'lease_owner': str(uuid.uuid4()),
                 'lease_expires_at': now+timedelta(seconds=LEASE_SECONDS), 'updated_at': now}},
        sort=[('created_at', 1)], return_document=ReturnDocument.AFTER)
    if job is None:
        return False
    try:
        {'sql': sql_stage, 'mongo': mongo_stage, 'files': files_stage, 'verify': finish_stage}[job['stage']](job)
        if job['status'] == 'running':
            checkpoint(job, status='queued', available_at=utcnow())
    except DeletionError as error:
        if error.code != 'lease_lost':
            checkpoint(job, status='failed', error=error.message, retryable=True)
    except Exception as error:
        logger.warning('student_deletion_failed stage=%s error_type=%s', job['stage'], type(error).__name__)
        checkpoint(job, status='failed', error='Не удалось завершить удаление на этапе '+job['stage']+'. Повторите операцию.', retryable=True)
    return True


def start_worker(app):
    global _started
    if not app.config.get('STUDENT_DELETION_WORKER_ENABLED', True):
        return
    with _start_lock:
        if _started:
            return
        _started = True
    stop = threading.Event()
    app.extensions['student_deletion_worker_stop'] = stop

    def loop():
        with app.app_context():
            while not stop.is_set():
                try:
                    busy = run_once()
                except Exception as error:
                    logger.warning('student_deletion_queue_unavailable error_type=%s', type(error).__name__)
                    busy = False
                stop.wait(0.2 if busy else 2)
    thread = threading.Thread(target=loop, daemon=True, name='student-deletion-worker')
    app.extensions['student_deletion_worker_thread'] = thread
    thread.start()
