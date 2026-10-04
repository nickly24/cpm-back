"""Targeted removal of personal fragments from shared JSON import receipts.

Shared batches are never deleted. Unattributed matching text is a rollout/data
repair blocker, rather than an excuse to erase somebody else's import row.
"""
from .common import DeletionError, digest, dumps, loads, rows, quoted

JSON_COLUMNS = {
    'user_import_sessions': ('preview_payload',),
    'user_import_jobs': ('errors', 'entities_created', 'summary'),
    'rating_recalc_jobs': ('errors',),
    'user_import_job_results': ('rows_data',),
    'classic_exam_assignment_import_sessions': ('preview_payload', 'commit_result'),
    'outside_exam_result_import_sessions': ('preview_payload', 'commit_result'),
    # Exam receipts have a separate command-type-aware idempotency policy.
}
ID_FIELDS = {'student_id', 'studentId', 'studentID', 'existing_student_id', 'target_student_id', 'privilegeBaselineStudentId'}
DROP = object()


def matches_id(value, student_id):
    text = str(value)
    return not isinstance(value, bool) and text.isascii() and text.isdigit() and int(text) == student_id


def explicit_owners(value):
    if not isinstance(value, dict):
        return set()
    raw = [value[key] for key in ID_FIELDS if value.get(key) is not None]
    if isinstance(value.get('student'), dict) and value['student'].get('id') is not None:
        raw.append(value['student']['id'])
    # Actual persisted exam previews retain input + baseline ID, not resolved.student.
    if isinstance(value.get('input'), dict):
        raw.extend(value['input'][key] for key in ID_FIELDS if value['input'].get(key) is not None)
    owners = set()
    for item in raw:
        if isinstance(item, bool) or not str(item).isascii() or not str(item).isdigit() or int(item) < 1:
            raise DeletionError('ambiguous_personal_snapshot', 'Некорректный владелец персональной строки журнала.', 503)
        owners.add(int(item))
    return owners


def owns(value, student_id, logins):
    owners = explicit_owners(value)
    if student_id in owners and len(owners) > 1:
        raise DeletionError('ambiguous_personal_snapshot', 'Противоречивые владельцы строки журнала.', 503)
    # Logins can be renamed and reused. They are not historical ownership proof.
    # Login-only fragments are caught by the unattributed-text gate below.
    return owners == {student_id}


def contains_unattributed(value, student_id, tokens):
    if isinstance(value, dict):
        owners = explicit_owners(value)
        if owners and student_id not in owners:
            return False  # Another student's explicitly owned row stays verbatim.
        return any(contains_unattributed(item, student_id, tokens) for item in value.values())
    if isinstance(value, list):
        return any(contains_unattributed(item, student_id, tokens) for item in value)
    return isinstance(value, str) and any(token and (value == token or (len(token) > 3 and token in value)) for token in tokens)


def scrub(value, student_id, logins, *, linked_rows=frozenset(), linked_sources=None, row_container=False):
    """Return DROP for one owned logical row; preserve all other rows verbatim."""
    if isinstance(value, list):
        result = []
        for item in value:
            clean = scrub(item, student_id, logins, linked_rows=linked_rows, linked_sources=linked_sources, row_container=True)
            if clean is not DROP:
                result.append(clean)
        return result
    if not isinstance(value, dict):
        return value
    owners = explicit_owners(value)
    linked = row_container and value.get('row') in linked_rows
    if linked:
        source = (linked_sources or {}).get(str(value.get('row')))
        # Session previews are editable. A reused row number alone is not proof.
        linked = bool(source and source.get('full_name') and all(value.get(field) == original for field, original in source.items()))
    if owners and student_id not in owners:
        if linked:
            raise DeletionError('ambiguous_personal_snapshot', 'Номер строки относится к другому владельцу.', 503)
        return value
    if owns(value, student_id, logins) or linked:
        return DROP
    if row_container and isinstance(value.get('values'), dict) and owns(value['values'], student_id, logins):
        return DROP
    result = {}
    for key, item in value.items():
        if key in {'student_ids_created', 'student_ids', 'studentIds'} and isinstance(item, list):
            result[key] = [v for v in item if not matches_id(v, student_id)]
        elif key == 'auth_usernames_created' and isinstance(item, list):
            result[key] = [v for v in item if v not in logins]
        else:
            clean = scrub(item, student_id, logins, linked_rows=linked_rows, linked_sources=linked_sources)
            # Do not retain the raw/source fields of a recognized logical import row.
            if clean is DROP and row_container:
                return DROP
            result[key] = None if clean is DROP else clean
    return result


def identity(sql):
    logins = {r.get('username') for r in sql.get('auth_users', []) if r.get('username')}
    logins |= {r.get('login') for r in sql.get('student_credentials', []) if r.get('login')}
    name = (sql.get('students') or [{}])[0].get('full_name')
    return logins, name


def collect_retained_fragments(cursor, columns, pks, student_id, sql):
    logins, name = identity(sql)
    linked_sessions = {}
    source_sessions = {}
    owned_logins_by_job = {}
    if 'user_import_job_results' in columns and 'user_import_jobs' in columns:
        for row in rows(cursor, 'SELECT r.rows_data,r.job_id,j.session_id FROM user_import_job_results r JOIN user_import_jobs j ON j.id=r.job_id'):
            for item in loads(row['rows_data']) or []:
                if owns(item, student_id, logins):
                    if item.get('row') is not None:
                        linked_sessions.setdefault(row['session_id'], set()).add(item['row'])
                        source_sessions.setdefault(row['session_id'], {})[str(item['row'])] = {field: item[field] for field in ('full_name', 'class', 'school_name', 'tg_name', 'proctor_name') if item.get(field) not in (None, '')}
                    if isinstance(item.get('login'), str) and item['login']:
                        owned_logins_by_job.setdefault(row['job_id'], set()).add(item['login'])
    # An immutable import result can establish a historical alias only as a
    # diagnostic clue, not permission to erase a login-only snapshot. Renaming
    # the account must not silently hide a known ambiguous historical reference.
    historical_logins = set().union(*owned_logins_by_job.values()) if owned_logins_by_job else set()
    diagnostic_logins = logins | historical_logins
    manifest, fingerprints = [], []
    for table, fields in JSON_COLUMNS.items():
        if table not in columns:
            continue
        present = {c['COLUMN_NAME'] for c in columns[table]}
        fields = [field for field in fields if field in present]
        if not fields:
            continue
        if not pks[table]:
            raise DeletionError('missing_primary_key', 'Нет ключа журнала ' + table, 503)
        # Directly owned rows are deleted whole. Updating their JSON after the
        # barrier would (correctly) be rejected by the OLD-owner write trigger.
        owned_keys = {dumps({field: row[field] for field in pks[table]}) for row in sql.get(table, [])}
        for row in rows(cursor, f'SELECT * FROM {quoted(table)}'):
            key = {field: row[field] for field in pks[table]}
            if dumps(key) in owned_keys:
                continue
            linked = linked_sessions.get(row.get('id'), set()) if table == 'user_import_sessions' else set()
            linked_sources = source_sessions.get(row.get('id'), {}) if table == 'user_import_sessions' else {}
            for field in fields:
                value = loads(row[field])
                scoped_logins = owned_logins_by_job.get(row.get('id'), set()) if table == 'user_import_jobs' else logins
                clean = scrub(value, student_id, scoped_logins, linked_rows=linked, linked_sources=linked_sources)
                if clean is DROP:
                    clean = {'student_deleted': True}
                if clean != value:
                    manifest.append({'table': table, 'key': key, 'column': field, 'linked_rows': sorted(linked), 'linked_sources': linked_sources, 'owned_logins': sorted(scoped_logins) if table == 'user_import_jobs' else []})
                    fingerprints.append({'table': table, 'key': key, 'column': field, 'hash': digest(value)})
                # Known name/login with no attributable row must not silently survive.
                if contains_unattributed(clean, student_id, [name, *diagnostic_logins]):
                    raise DeletionError('unattributed_personal_snapshot', 'Не удалось безопасно сопоставить персональную строку журнала ' + table + '. Требуется проверка записи.', 503)
    return {'manifest': manifest, 'fingerprint': fingerprints}


def apply_retained_fragments(cursor, manifest, student_id, logins, companions=()):
    # Guard triggers validate the ENTIRE NEW row. Group every affected column and
    # independently accepted companion plan by exact row, then update once.
    grouped = {}
    for entry in manifest:
        table, field, key = entry['table'], entry['column'], entry['key']
        if table not in JSON_COLUMNS or field not in JSON_COLUMNS[table]:
            raise DeletionError('invalid_manifest', 'Неподдерживаемая запись манифеста.', 503)
        group = grouped.setdefault((table, dumps(key)), {'table': table, 'key': key, 'plans': {}})
        group['plans'].setdefault(field, []).append((student_id, logins, entry))
    for companion in companions:
        for entry in companion['retained']:
            table, field, key = entry['table'], entry['column'], entry['key']
            group = grouped.get((table, dumps(key)))
            if group is None:
                continue  # Never broaden this stage to unrelated shared rows.
            if table not in JSON_COLUMNS or field not in JSON_COLUMNS[table]:
                raise DeletionError('invalid_manifest', 'Неподдерживаемая запись манифеста.', 503)
            group['plans'].setdefault(field, []).append((companion['student_id'], companion['logins'], entry))
    for group in grouped.values():
        table, key, plans = group['table'], group['key'], group['plans']
        where = ' AND '.join(quoted(k) + '=%s' for k in key)
        fields = sorted(plans)
        result = rows(cursor, f"SELECT {','.join(quoted(field) for field in fields)} FROM {quoted(table)} WHERE {where} FOR UPDATE", tuple(key.values()))
        if not result:
            continue
        changes = {}
        for field in fields:
            value = loads(result[0][field])
            clean = value
            for target, target_logins, plan in plans[field]:
                clean = scrub(clean, target, set(plan.get('owned_logins', [])) if table == 'user_import_jobs' else target_logins, linked_rows=set(plan.get('linked_rows', [])), linked_sources=plan.get('linked_sources', {}))
                if clean is DROP:
                    clean = {'student_deleted': True}
            if clean != value:
                changes[field] = dumps(clean)
        if changes:
            assignments = ','.join(quoted(field) + '=%s' for field in changes)
            cursor.execute(f'UPDATE {quoted(table)} SET {assignments} WHERE {where}', (*changes.values(), *key.values()))
