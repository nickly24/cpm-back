"""Retired homework personal data: ownership, not feature restoration.

Only documented typed references may establish ownership. Identifier-only scans
are intentionally bounded and fail closed: an unowned legacy outbox row cannot
be silently attributed to a numeric student/message ID. Diagnostics contain only
table/reason counts, never row IDs, JSON, message text or subscription secrets.
"""
from collections import Counter
import json
import re
import uuid

from .common import DeletionError, dumps, quoted, rows

LIMIT = 100000
USER_ROLES = {'student', 'admin', 'proctor', 'examinator', 'supervisor', 'staff_admin'}
# Producers verified as read-only history: 98b5b74,
# cpm_back/services/homework_files/realtime_events.py, cpm_back/realtime.py,
# cpm_back/blueprints/homework_chat_bp.py; older supplied schema-audit history
# uses message.created {message_id} and message.read without a JSON payload.
OUTBOX_EVENTS = {'message.created', 'message.read', 'typing.changed',
                 'submission.changed', 'job.progress', 'notification.created'}
LEGACY_FIELDS = {
    'homework_chat_threads': ('id', 'homework_id', 'student_id'),
    'homework_chat_messages': ('id', 'thread_id', 'sender_role', 'sender_id'),
    'homework_chat_reads': ('thread_id', 'reader_role', 'reader_id', 'last_message_id'),
    'homework_chat_admin_followers': ('thread_id', 'admin_id'),
    'homework_realtime_presence': ('thread_id', 'actor_role', 'actor_id'),
    'homework_realtime_outbox': ('id', 'thread_id', 'event_type', 'entity_id', 'payload_json'),
    'notifications': ('id', 'recipient_role', 'recipient_id', 'student_id', 'thread_id'),
    'push_subscriptions': ('id', 'user_role', 'user_id'),
}
LEGACY_TABLES = set(LEGACY_FIELDS)
LEGACY_PKS = {table: ('id',) for table in LEGACY_TABLES}
LEGACY_PKS.update({
    'homework_chat_reads': ('thread_id', 'reader_role', 'reader_id'),
    'homework_chat_admin_followers': ('thread_id', 'admin_id'),
    'homework_realtime_presence': ('thread_id', 'actor_role', 'actor_id'),
})
LEGACY_DEPENDENCIES = {
    (table, 'homework_chat_threads') for table in (
        'homework_chat_messages', 'homework_chat_reads', 'homework_chat_admin_followers',
        'homework_realtime_presence', 'homework_realtime_outbox', 'notifications')
} | {('homework_chat_reads', 'homework_chat_messages'),
     ('homework_realtime_outbox', 'homework_chat_messages'),
     ('homework_realtime_outbox', 'notifications')}
ROOM = re.compile(r'^user:([a-z_]+):([1-9][0-9]*)$')


def _id(value, *, zero=False):
    if isinstance(value, bool):
        raise ValueError()
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r'0|[1-9][0-9]*', value):
        result = int(value)
    else:
        raise ValueError()
    if result < (0 if zero else 1) or result > 18446744073709551615:
        raise ValueError()
    return result


def _key(row, table):
    return tuple(row[k] for k in LEGACY_PKS[table])


def _scan(cursor, table, fields):
    result = rows(cursor, 'SELECT ' + ','.join(quoted(field) for field in fields)
                  + ' FROM ' + quoted(table) + ' LIMIT 100001')
    if len(result) > LIMIT:
        raise DeletionError('legacy_inventory_too_large',
                            'Нужна пакетная проверка старого архива: ' + table + '.', 503)
    return result


def validate_legacy_schema(columns, pks=None):
    for table, fields in LEGACY_FIELDS.items():
        if table not in columns:
            continue
        actual = {c['COLUMN_NAME'] for c in columns[table]}
        if not set(fields) <= actual or (pks is not None and set(pks[table]) != set(LEGACY_PKS[table])):
            raise DeletionError('unsupported_legacy_schema',
                                'Нужна проверка структуры старой персональной таблицы: ' + table + '.', 503)


class Ownership:
    def __init__(self, data, student_id, context, submissions, jobs):
        self.data, self.sid = data, _id(student_id)
        self.issues = Counter()
        self.selected = {table: [] for table in data}
        context = context or {}
        if context and (context.get('version') != 1 or context.get('student_id') != self.sid):
            raise DeletionError('invalid_legacy_context', 'Нужна проверка контекста старого архива.', 503)
        self.context = context
        self.threads = {r['id']: r for r in data.get('homework_chat_threads', [])}
        self.messages = {r['id']: r for r in data.get('homework_chat_messages', [])}
        self.notifications = {r['id']: r for r in data.get('notifications', [])}
        self.submission_rows = {r['id']: r for r in submissions}
        self.submissions = {r['id']: r['student_id'] for r in submissions}
        self.job_rows = {str(r['id']): r for r in jobs}
        self.jobs = {str(r['id']): r['student_id'] for r in jobs}
        self.old_threads = set(context.get('thread_ids', []))
        self.old_messages = {r['id']: r['thread_id'] for r in context.get('message_threads', [])}
        self.old_notifications = set(context.get('notification_ids', []))
        self.old_submissions = set(context.get('submission_ids', []))
        self.old_jobs = set(context.get('job_ids', []))
        self.notification_owners = {}

    def issue(self, table, reason):
        self.issues[(table, reason)] += 1

    def thread_owner(self, value):
        if value is None:
            return None
        ident = _id(value)
        row = self.threads.get(ident)
        if row:
            owner = _id(row['student_id'])
            if ident in self.old_threads and owner != self.sid:
                raise ValueError()
            return owner
        return self.sid if ident in self.old_threads else None

    def actor_owner(self, role, value, *, system=False):
        if system and role == 'system' and value is None:
            return None
        if role not in USER_ROLES:
            raise ValueError()
        ident = _id(value)
        return ident if role == 'student' else None

    def message(self, value, *, zero=False):
        ident = _id(value, zero=zero)
        if ident == 0:
            return None, None
        message = self.messages.get(ident)
        if message:
            thread = _id(message['thread_id'])
            owner = self.thread_owner(thread)
            sender = self.actor_owner(message['sender_role'], message['sender_id'], system=True)
            owners = {o for o in (owner, sender) if o is not None}
            if len(owners) != 1 or (ident in self.old_messages and self.old_messages[ident] != thread):
                raise ValueError()
            return next(iter(owners)), thread
        if ident in self.old_messages:
            return self.sid, self.old_messages[ident]
        raise LookupError()

    def choose(self, table, row, owners, *, require_owner=True):
        owners = {owner for owner in owners if owner is not None}
        if len(owners) > 1:
            self.issue(table, 'contradictory_ownership')
        elif not owners and require_owner:
            self.issue(table, 'unresolved_ownership')
        elif self.sid in owners:
            self.selected[table].append(row)
        return owners

    def inspect(self, *, strict=True):
        for table, values in self.data.items():
            if table == 'homework_realtime_outbox':
                continue  # Notifications and message maps must be resolved first.
            for row in values:
                try:
                    if table == 'homework_chat_threads':
                        owners = [_id(row['student_id'])]
                        if row['id'] in self.old_threads:
                            owners.append(self.sid)
                    elif table == 'homework_chat_messages':
                        owners = [self.thread_owner(row['thread_id']),
                                  self.actor_owner(row['sender_role'], row['sender_id'], system=True)]
                    elif table in {'homework_chat_reads', 'homework_realtime_presence'}:
                        prefix = 'reader' if table == 'homework_chat_reads' else 'actor'
                        owners = [self.thread_owner(row['thread_id']),
                                  self.actor_owner(row[prefix + '_role'], row[prefix + '_id'])]
                        if table == 'homework_chat_reads' and _id(row['last_message_id'], zero=True):
                            owner, thread = self.message(row['last_message_id'])
                            if thread != row['thread_id']:
                                self.issue(table, 'last_message_thread_mismatch')
                            owners.append(owner)
                    elif table == 'homework_chat_admin_followers':
                        _id(row['admin_id'])
                        owners = [self.thread_owner(row['thread_id'])]
                    elif table == 'notifications':
                        owners = [self.actor_owner(row['recipient_role'], row['recipient_id']),
                                  _id(row['student_id']) if row['student_id'] is not None else None,
                                  self.thread_owner(row['thread_id'])]
                        # A dangling unqualified thread may concern any student.
                        if row['thread_id'] is not None and not any(o is not None for o in owners):
                            raise LookupError()
                    else:  # push subscriptions have role-qualified ownership only.
                        owners = [self.actor_owner(row['user_role'], row['user_id'])]
                    resolved = self.choose(table, row, owners,
                                           require_owner=table not in {'notifications', 'push_subscriptions'})
                    if table == 'notifications':
                        self.notification_owners[row['id']] = resolved
                except LookupError:
                    self.issue(table, 'orphan_reference')
                except (ValueError, TypeError, KeyError):
                    self.issue(table, 'invalid_or_conflicting_reference')
        for row in self.data.get('homework_realtime_outbox', []):
            try:
                owners = self.outbox(row)
                self.choose('homework_realtime_outbox', row, owners, require_owner=False)
            except LookupError:
                self.issue('homework_realtime_outbox', 'orphan_reference')
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                self.issue('homework_realtime_outbox', 'unsupported_or_conflicting_event')
        if self.issues and strict:
            diagnostic = '; '.join(f'{table}.{reason}={count}' for (table, reason), count in sorted(self.issues.items()))
            raise DeletionError('legacy_ownership_unresolved',
                                'Удаление заблокировано: исправьте связи старого архива или расширьте проверенные варианты событий. '
                                + diagnostic + '.', 503)
        return self.selected

    def outbox(self, row):
        event = row['event_type']
        if event not in OUTBOX_EVENTS:
            raise ValueError()
        raw = row['payload_json']
        payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError()
        thread_id = row['thread_id']
        thread_owner = self.thread_owner(thread_id)
        owners = [thread_owner]
        entity = row['entity_id']
        if event in {'message.created', 'message.read'}:
            allowed = {'message_id'} if event == 'message.created' else {'reader_role', 'reader_id', 'last_message_id'}
            if set(payload) not in (set(), allowed):
                raise ValueError()
            explicit = payload.get('message_id') if event == 'message.created' else payload.get('last_message_id')
            if payload and explicit is None:
                raise ValueError()
            if entity is not None and explicit is not None and _id(entity, zero=True) != _id(explicit, zero=True):
                raise ValueError()
            reference = entity if entity is not None else explicit
            owner, message_thread = self.message(reference, zero=event == 'message.read')
            if thread_id is not None and message_thread is not None and _id(thread_id) != message_thread:
                raise ValueError()
            if message_thread is None and thread_owner is None:
                raise LookupError()
            owners.append(owner)
            if 'reader_role' in payload:
                owners.append(self.actor_owner(payload['reader_role'], payload['reader_id']))
        elif event == 'typing.changed':
            if set(payload) != {'actor_role', 'actor_id', 'active', 'expires_in_ms'} or entity is not None:
                raise ValueError()
            if thread_owner is None:
                raise LookupError()
            if not isinstance(payload['active'], bool):
                raise ValueError()
            _id(payload['expires_in_ms'])
            owners.append(self.actor_owner(payload['actor_role'], payload['actor_id']))
        else:
            if set(payload) != {'room', 'data'} or not isinstance(payload['data'], dict) or thread_id is not None:
                raise ValueError()
            room = ROOM.fullmatch(payload['room']) if isinstance(payload['room'], str) else None
            if room is None or room[1] not in USER_ROLES:
                raise ValueError()
            role, recipient = room[1], _id(room[2])
            room_owner = self.actor_owner(role, recipient)
            owners.append(room_owner)
            data = payload['data']
            if event == 'submission.changed':
                if set(data) != {'homework_id', 'student_id', 'submission_id'} or role != 'student':
                    raise ValueError()
                _id(data['homework_id'])
                owners.append(_id(data['student_id']))
                if (entity is None) != (data['submission_id'] is None):
                    raise ValueError()
                if entity is not None:
                    ident = _id(entity)
                    if ident != _id(data['submission_id']):
                        raise ValueError()
                    owner = self.submissions.get(ident, self.sid if ident in self.old_submissions else None)
                    if owner is None:
                        raise LookupError()
                    if ident in self.submission_rows and _id(data['homework_id']) != _id(self.submission_rows[ident]['homework_id']):
                        raise ValueError()
                    owners.append(_id(owner))
            elif event == 'job.progress':
                if set(data) != {'job'} or not isinstance(data['job'], dict) or entity is not None or role != 'student':
                    raise ValueError()
                job = data['job']
                if set(job) != {'id', 'homework_id', 'status', 'stage', 'progress', 'error_code'}:
                    raise ValueError()
                if not isinstance(job['id'], str) or str(uuid.UUID(job['id'])) != job['id']:
                    raise ValueError()
                if any(not isinstance(job[field], str) or not job[field] for field in ('status', 'stage')):
                    raise ValueError()
                if job['error_code'] is not None and not isinstance(job['error_code'], str):
                    raise ValueError()
                if isinstance(job['progress'], bool) or not isinstance(job['progress'], int) or not 0 <= job['progress'] <= 100:
                    raise ValueError()
                owner = self.jobs.get(job['id'], self.sid if job['id'] in self.old_jobs else None)
                if owner is None:
                    raise LookupError()
                _id(job['homework_id'])
                if job['id'] in self.job_rows and _id(job['homework_id']) != _id(self.job_rows[job['id']]['homework_id']):
                    raise ValueError()
                owners.append(_id(owner))
            else:  # entity_id means notification ONLY for notification.created.
                if set(data) != {'entity_id'} or _id(entity) != _id(data['entity_id']):
                    raise ValueError()
                ident = _id(entity)
                notice = self.notifications.get(ident)
                if notice:
                    if notice['recipient_role'] != role or _id(notice['recipient_id']) != recipient:
                        raise ValueError()
                    owners.extend(self.notification_owners.get(ident, set()))
                elif ident in self.old_notifications:
                    owners.append(self.sid)
                else:
                    raise LookupError()
        if thread_id is not None and thread_owner is None:
            # A typed message may prove an orphan thread's owner, but the thread
            # number must agree with that exact message reference above.
            if event not in {'message.created', 'message.read'} or not any(o is not None for o in owners):
                raise LookupError()
        return owners

    def manifest_context(self):
        selected = self.selected
        return {
            'version': 1, 'student_id': self.sid, 'tables': sorted(self.data),
            'thread_ids': sorted(self.old_threads | {r['id'] for r in selected.get('homework_chat_threads', [])}),
            'message_threads': sorted([{'id': key, 'thread_id': value} for key, value in
                {**self.old_messages, **{r['id']: r['thread_id'] for r in selected.get('homework_chat_messages', [])}}.items()], key=lambda r: r['id']),
            'notification_ids': sorted(self.old_notifications | {r['id'] for r in selected.get('notifications', [])}),
            'submission_ids': sorted(self.old_submissions | {key for key, owner in self.submissions.items() if owner == self.sid}),
            'job_ids': sorted(self.old_jobs | {key for key, owner in self.jobs.items() if owner == self.sid}),
            'keys': {table: sorted([{key: row[key] for key in LEGACY_PKS[table]} for row in values], key=dumps)
                     for table, values in selected.items()},
        }


def _resolve(cursor, columns, student_id, context=None, *, strict=True):
    validate_legacy_schema(columns)
    data = {table: _scan(cursor, table, fields) for table, fields in LEGACY_FIELDS.items() if table in columns}
    # Structural references only; no private bodies, secrets or file content in
    # scans across students. Full selected rows are fetched after ownership gates.
    if not data:
        return Ownership({}, student_id, context, [], [])
    submissions = _scan(cursor, 'homework_submissions', ('id', 'student_id', 'homework_id')) if 'homework_submissions' in columns else []
    jobs = _scan(cursor, 'homework_file_jobs', ('id', 'student_id', 'homework_id')) if 'homework_file_jobs' in columns else []
    graph = Ownership(data, student_id, context, submissions, jobs)
    graph.inspect(strict=strict)
    return graph


def collect_legacy(cursor, columns, student_id, sql=None, context=None, *, strict=True):
    graph = _resolve(cursor, columns, student_id, context, strict=strict)
    selected = {}
    for table, values in graph.selected.items():
        selected[table] = []
        for start in range(0, len(values), 500):
            batch = values[start:start + 500]
            where = ' OR '.join('(' + ' AND '.join(quoted(key) + '=%s' for key in LEGACY_PKS[table]) + ')' for _ in batch)
            args = tuple(value[key] for value in batch for key in LEGACY_PKS[table])
            selected[table].extend(rows(cursor, f'SELECT * FROM {quoted(table)} WHERE {where}', args))
        selected[table].sort(key=dumps)
    warnings = [{'source': 'mysql', 'target': table, 'count': count,
                 'message': 'Неясные связи старого архива ('+reason+'). Проверьте эти записи вручную.'}
                for (table, reason), count in sorted(graph.issues.items())]
    return {'sql': selected, 'context': graph.manifest_context(), 'warnings': warnings}


def verify_legacy_residual(cursor, student_id, context):
    columns = {}
    for row in rows(cursor, 'SELECT TABLE_NAME,COLUMN_NAME,COLUMN_KEY,ORDINAL_POSITION FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME,ORDINAL_POSITION'):
        columns.setdefault(row['TABLE_NAME'], []).append(row)
    if context and set(context.get('tables', [])) - columns.keys():
        raise DeletionError('legacy_verification_unavailable', 'Исчезла таблица старого архива; требуется проверка схемы.', 503)
    graph = _resolve(cursor, columns, student_id, context)
    residual = Counter({table: len(values) for table, values in graph.selected.items() if values})
    # Exact saved keys remain authoritative even if an ownership field was
    # rewritten after the snapshot. Never silently treat a moved row as erased.
    for table, keys in (context or {}).get('keys', {}).items():
        if table not in LEGACY_TABLES or table not in columns:
            raise DeletionError('invalid_legacy_context', 'Нужна проверка контекста старого архива.', 503)
        live = {_key(row, table) for row in graph.data.get(table, [])}
        count = sum(tuple(key[field] for field in LEGACY_PKS[table]) in live for key in keys)
        residual[table] = max(residual[table], count)
    if any(residual.values()):
        detail = '; '.join(f'{table}={count}' for table, count in sorted(residual.items()) if count)
        raise DeletionError('legacy_residual_records', 'Остались записи старого персонального архива: ' + detail + '.', 503)
