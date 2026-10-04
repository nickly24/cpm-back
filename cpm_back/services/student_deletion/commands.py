"""Preserve exam idempotency while erasing proven personal command targets.

Receipt formats are bound to the current producer's command_type. A shared
exam/import receipt is never treated as a generic student-owned JSON container.
Unknown or conflicting personal formats stop deletion before destructive work.
"""
from .common import DeletionError, digest, dumps, loads, rows, one
from .retention import contains_unattributed, identity

COMMON_KEYS = {'commandId', 'outcome'}
PERSONAL = {
    'POST:exams_v2.assignments_collection': {'assignmentId', 'studentId'},
    'PUT:exams_v2.assignment_detail': {'assignmentId'},
    'DELETE:exams_v2.assignment_detail': {'assignmentId'},
    'PUT:exams_v2.student_privilege': {'studentId'},
    'DELETE:exams_v2.student_privilege': {'studentId'},
    'POST:exams_v2.outside_results': {'resultId', 'studentId'},
    'PATCH:exams_v2.outside_result_detail': {'resultId'},
    'DELETE:exams_v2.outside_result_detail': {'resultId'},
    'POST:exams_v2.classic_attempt_appeals': {'appealId', 'attemptId', 'studentId'},
    'POST:exams_v2.classic_student_retake': {'assignmentId', 'studentId'},
    'DELETE:exams_v2.classic_history_delete': set(),
}
SHARED = {
    'POST:exams_v2.exams_collection': {'examId'},
    'PATCH:exams_v2.exam_direction': {'examId'},
    'PATCH:exams_v2.exam_training_settings': {'examId'},
    'PATCH:exams_v2.classic_config': {'examId'},
    'PUT:exams_v2.classic_scoring': {'examId'},
    'POST:exams_v2.parts_collection': {'partId'},
    'PATCH:exams_v2.part_detail': {'partId'},
    'DELETE:exams_v2.part_detail': {'partId'},
    'POST:exams_v2.questions_collection': {'questionId'},
    'PATCH:exams_v2.question_detail': {'questionId'},
    'DELETE:exams_v2.question_detail': {'questionId'},
    'POST:exams_v2.commissions_collection': {'commissionId'},
    'PUT:exams_v2.commission_detail': {'commissionId'},
    'DELETE:exams_v2.commission_detail': {'commissionId'},
    'PATCH:exams_v2.outside_config': {'examId'},
    'DELETE:exams_v2.full_exam_delete': set(),
    'POST:exam_imports.parse': {'sessionId'},
    'PUT:exam_imports.session': {'sessionId', 'previewVersion'},
    'POST:exam_imports.commit': {'sessionId', 'committed'},
}
REQUIRED_COLUMNS = {'id', 'actor_role', 'actor_id', 'idempotency_key', 'command_type',
                    'target_exam_id', 'target_student_id', 'target_attempt_id', 'request_hash',
                    'receipt', 'tombstone', 'created_at', 'expires_at'}


def _id(value):
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit() or int(value) < 1:
        raise ValueError('invalid_reference')
    return int(value)


def _blocked(counts):
    detail = ','.join(key + '=' + str(value) for key, value in sorted(counts.items()) if value)
    error = DeletionError('exam_command_scope_unverified', 'Требуется проверка квитанций экзаменов: ' + detail, 503)
    error.counts = counts
    raise error


def collect_command_sanitizations(cursor, columns, student_id, sql):
    if 'exam_admin_commands' not in columns:
        return {'actions': [], 'fingerprint': []}
    fields = {c['COLUMN_NAME'] if isinstance(c, dict) else c for c in columns['exam_admin_commands']}
    if not REQUIRED_COLUMNS <= fields:
        _blocked({'missing_command_columns': len(REQUIRED_COLUMNS - fields)})
    logins, name = identity(sql)
    commands = rows(cursor, 'SELECT * FROM exam_admin_commands ORDER BY id LIMIT 100001')
    if len(commands) > 100000:
        _blocked({'command_inventory_too_large': 1})
    counts, actions, fingerprints, cache = {}, [], [], {}
    # Prior route-produced POST receipts retain immutable student ownership even
    # when a later ordinary DELETE removed the assignment/result itself. Typed
    # historical proofs may resolve sibling PATCH receipts; numeric IDs from a
    # different entity type or unknown command never become ownership evidence.
    historical_owners = {}
    for command in commands:
        try:
            receipt = loads(command['receipt'])
            expected = PERSONAL.get(command['command_type'])
            if expected is None or not isinstance(receipt, dict) or not expected <= set(receipt) or not set(receipt) <= expected | COMMON_KEYS:
                continue
            explicit = {_id(value) for value in (command['target_student_id'], receipt.get('studentId')) if value is not None}
            if len(explicit) != 1:
                continue
            for field in ('assignmentId', 'attemptId', 'resultId', 'appealId'):
                if field in receipt:
                    historical_owners.setdefault((field, _id(receipt[field])), set()).update(explicit)
        except (TypeError, ValueError):
            continue  # The validation pass below reports the malformed row.

    def issue(code):
        counts[code] = counts.get(code, 0) + 1

    def owner(kind, value, optional_deleted=False):
        ident = _id(value)
        key = (kind, ident)
        if key not in cache:
            query = {
                'assignmentId': 'SELECT student_id FROM classic_exam_assignments WHERE id=%s',
                'attemptId': 'SELECT student_id FROM classic_exam_attempts WHERE id=%s',
                'resultId': 'SELECT student_id FROM exam_sessions WHERE id=%s',
                'appealId': 'SELECT a.student_id FROM classic_exam_appeals p JOIN classic_exam_attempts a ON a.id=p.attempt_id WHERE p.id=%s',
            }[kind]
            cache[key] = one(cursor, query, (ident,))
        row = cache[key]
        if row is None:
            proof = historical_owners.get(key, set())
            if len(proof) > 1:
                raise ValueError('conflicting_command_owners')
            if proof:
                return next(iter(proof))
            if optional_deleted:
                return None
            raise LookupError('orphan_command_reference')
        actual = _id(row['student_id'])
        proof = historical_owners.get(key, set())
        if proof and proof != {actual}:
            raise ValueError('conflicting_command_owners')
        return actual

    for command in commands:
        try:
            receipt = loads(command['receipt'])
            if receipt is not None and not isinstance(receipt, dict):
                raise ValueError('invalid_receipt_shape')
            kind = command['command_type']
            expected = PERSONAL.get(kind, SHARED.get(kind))
            if expected is None:
                raise ValueError('unknown_command_type')
            if receipt is not None:
                if not expected <= set(receipt) or not set(receipt) <= expected | COMMON_KEYS:
                    raise ValueError('unsupported_receipt_fields')
                if 'commandId' in receipt and _id(receipt['commandId']) != int(command['id']):
                    raise ValueError('conflicting_command_id')
                if 'outcome' in receipt and receipt['outcome'] not in {'applied', 'deleted'}:
                    raise ValueError('unsupported_command_outcome')
            elif not command['tombstone']:
                # A committed reservation without a finished receipt has no safe replay state.
                raise ValueError('unfinished_command_receipt')
            owners = set()
            if command['target_student_id'] is not None:
                owners.add(_id(command['target_student_id']))
            if receipt and 'studentId' in receipt:
                owners.add(_id(receipt['studentId']))
            if command['target_attempt_id'] is not None:
                ref = owner('attemptId', command['target_attempt_id'], optional_deleted=bool(command['tombstone']) or bool(owners))
                if ref is not None:
                    owners.add(ref)
            if receipt:
                if 'studentId' in receipt:
                    owners.add(_id(receipt['studentId']))
                deleted = receipt.get('outcome') == 'deleted' or bool(owners)
                for field in ('assignmentId', 'attemptId', 'resultId', 'appealId'):
                    if field in receipt:
                        ref = owner(field, receipt[field], optional_deleted=deleted)
                        if ref is not None:
                            owners.add(ref)
            if len(owners) > 1:
                raise ValueError('conflicting_command_owners')
            if kind in SHARED and owners:
                raise ValueError('shared_command_has_personal_target')
            if owners == {student_id}:
                # Only a proven singular command is tombstoned. Shared exam and
                # import session receipts remain byte-for-byte unchanged.
                actions.append({'id': int(command['id']), 'mode': 'tombstone', 'fingerprint': digest(command)})
                fingerprints.append({'id': int(command['id']), 'hash': digest(command)})
            elif contains_unattributed(receipt, student_id, [name, *logins]):
                raise ValueError('unattributed_command_receipt')
        except (ValueError, TypeError, LookupError) as error:
            issue(str(error) if str(error) in {
                'invalid_reference', 'invalid_receipt_shape', 'unknown_command_type',
                'unsupported_receipt_fields', 'conflicting_command_id', 'unsupported_command_outcome',
                'unfinished_command_receipt', 'orphan_command_reference', 'conflicting_command_owners',
                'shared_command_has_personal_target', 'unattributed_command_receipt'} else 'invalid_command_receipt')
    if counts:
        _blocked(counts)
    return {'actions': actions, 'fingerprint': fingerprints}


def sanitize_commands(cursor, actions):
    for action in actions:
        if action.get('mode') != 'tombstone' or type(action.get('id')) is not int:
            raise DeletionError('invalid_command_manifest', 'Некорректный манифест квитанции.', 503)
        command = one(cursor, 'SELECT * FROM exam_admin_commands WHERE id=%s FOR UPDATE', (action['id'],))
        if command is None:
            raise DeletionError('command_receipt_missing', 'Пропала квитанция повторного запроса; требуется проверка.', 503)
        clean = command['tombstone'] and command['receipt'] is None and command['target_student_id'] is None and command['target_attempt_id'] is None
        if not clean and digest(command) != action['fingerprint']:
            raise DeletionError('command_receipt_changed', 'Квитанция изменилась после подтверждения.', 503)
        if not clean:
            cursor.execute('UPDATE exam_admin_commands SET receipt=NULL,tombstone=1,target_student_id=NULL,target_attempt_id=NULL WHERE id=%s', (action['id'],))
    return len(actions)


def verify_sanitized_commands(cursor, command_ids):
    for ident in command_ids:
        command = one(cursor, 'SELECT tombstone,receipt,target_student_id,target_attempt_id FROM exam_admin_commands WHERE id=%s', (ident,))
        if not command or not command['tombstone'] or command['receipt'] is not None or command['target_student_id'] is not None or command['target_attempt_id'] is not None:
            raise DeletionError('command_tombstone_unverified', 'Защитная квитанция не подтверждена.', 503)
