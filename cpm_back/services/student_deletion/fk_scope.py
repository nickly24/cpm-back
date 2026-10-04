"""Fail-closed ownership closure for the actual FK graph, independent of CASCADE.

Both directions matter: an unselected row must never be cascaded/SET NULL, and a
selected row must not reach a different student's personal parent. All persisted
context is structural identifiers only. Diagnostics expose counts, not row data.
"""
from collections import Counter

from .common import DeletionError, dumps, quoted, rows

LIMIT = 100000
FK_QUERY = '''SELECT TABLE_SCHEMA,TABLE_NAME,CONSTRAINT_NAME,COLUMN_NAME,ORDINAL_POSITION,
    REFERENCED_TABLE_SCHEMA,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME
    FROM information_schema.KEY_COLUMN_USAGE
    WHERE (TABLE_SCHEMA=DATABASE() OR REFERENCED_TABLE_SCHEMA=DATABASE())
    AND REFERENCED_TABLE_NAME IS NOT NULL
    ORDER BY TABLE_SCHEMA,TABLE_NAME,CONSTRAINT_NAME,ORDINAL_POSITION'''
# These references are present in the audited DDL without their own FKs. The
# round's second parent also needs same-attempt validation even within one owner.
SEMANTIC = (
    ('classic_exam_attempts', 'current_presented_question_id', 'classic_exam_presented_questions', 'id', 'attempt_id'),
    ('classic_exam_presented_questions', 'replaces_presented_question_id', 'classic_exam_presented_questions', 'attempt_id', 'attempt_id'),
    ('classic_exam_vote_rounds', 'presented_question_id', 'classic_exam_presented_questions', 'attempt_id', 'attempt_id'),
)


def _fail(counts, code='student_fk_scope_conflict'):
    detail = '; '.join(f'{table}.{reason}={count}' for (table, reason), count in sorted(counts.items()) if count)
    error = DeletionError(code, 'Удаление заблокировано: исправьте межличностные или неполные связи записей. ' + detail + '.', 503)
    error.counts = {f'{table}.{reason}': count for (table, reason), count in sorted(counts.items()) if count}
    raise error


def grouped_foreign_keys(fks):
    """Group by actual constraint, retaining composite column correspondence."""
    grouped = {}
    for index, fk in enumerate(fks):
        if not fk.get('REFERENCED_TABLE_NAME'):
            continue
        table, parent = fk['TABLE_NAME'], fk['REFERENCED_TABLE_NAME']
        if fk.get('TABLE_SCHEMA') and fk.get('REFERENCED_TABLE_SCHEMA') and fk['TABLE_SCHEMA'] != fk['REFERENCED_TABLE_SCHEMA']:
            _fail(Counter({(table, 'cross_schema_dependency'): 1}), 'student_fk_schema_unsupported')
        # Old driver fixtures omit constraint metadata and model only single FKs.
        name = fk.get('CONSTRAINT_NAME') or '__single_' + str(index)
        key = (fk.get('TABLE_SCHEMA', ''), table, name)
        grouped.setdefault(key, []).append(fk)
    result = []
    for key, values in sorted(grouped.items()):
        values.sort(key=lambda fk: int(fk.get('ORDINAL_POSITION') or 1))
        parents = {fk['REFERENCED_TABLE_NAME'] for fk in values}
        ordinals = [int(fk.get('ORDINAL_POSITION') or 1) for fk in values]
        if len(parents) != 1 or len(set(ordinals)) != len(ordinals):
            _fail(Counter({(key[1], 'invalid_fk_metadata'): 1}), 'student_fk_schema_unsupported')
        result.append({'table': key[1], 'name': key[2], 'parent': values[0]['REFERENCED_TABLE_NAME'],
                       'columns': [fk['COLUMN_NAME'] for fk in values],
                       'parent_columns': [fk['REFERENCED_COLUMN_NAME'] for fk in values]})
    return result


def _tuple(row, fields):
    return tuple(row[field] for field in fields)


def _identity(row, pks):
    return dumps(_tuple(row, pks))


def _matching(cursor, table, fields, values, projection):
    """Bounded composite matching; NULL references do not match SQL FKs."""
    values = sorted({value for value in values if all(v is not None for v in value)}, key=dumps)
    result = []
    for offset in range(0, len(values), 300):
        batch = values[offset:offset + 300]
        where = ' OR '.join('(' + ' AND '.join(quoted(field) + '=%s' for field in fields) + ')' for _ in batch)
        args = tuple(value for item in batch for value in item)
        query = 'SELECT ' + ','.join(quoted(field) for field in projection) + ' FROM ' + quoted(table)
        result.extend(rows(cursor, query + ' WHERE ' + where + ' LIMIT 100001', args))
        if len(result) > LIMIT:
            _fail(Counter({(table, 'scope_check_too_large'): 1}), 'student_fk_scope_unavailable')
    return result


def check_fk_scope(cursor, columns, pks, fks, selected, *, original=None):
    """Validate live selected rows; original authorizes only exact saved keys.

    At initial collection original is omitted. Before later destructive stages,
    absent rows from completed stages remain usable as reference evidence only.
    New rows are never added to authorized scope by this check.
    """
    original = selected if original is None else original
    constraints = grouped_foreign_keys(fks)
    issues = Counter()
    available = {table: {col['COLUMN_NAME'] for col in fields} for table, fields in columns.items()}
    authorized = {table: {_identity(row, pks[table]) for row in values} for table, values in original.items()}
    # Existing rows supersede historic reference values. Missing rows are retained
    # only so a post-unlink late child cannot escape the ownership check.
    parents = {}
    for table, historic in original.items():
        by_key = {_identity(row, pks[table]): row for row in historic}
        by_key.update({_identity(row, pks[table]): row for row in selected.get(table, [])})
        parents[table] = list(by_key.values())
    for fk in constraints:
        table, parent = fk['table'], fk['parent']
        if parent not in original:
            continue  # Shared definitions are not personal deletion parents.
        if table not in columns or parent not in columns:
            issues[(table, 'missing_dependency_table')] += 1
            continue
        parent_values = {_tuple(row, fk['parent_columns']) for row in parents[parent]}
        if parent_values:
            projection = list(dict.fromkeys([*pks.get(table, []), *fk['columns']]))
            incoming = _matching(cursor, table, fk['columns'], parent_values, projection)
            if not pks.get(table):
                issues[(table, 'incoming_without_primary_key')] += len(incoming)
            else:
                issues[(table, 'incoming_outside_authorized_scope')] += sum(
                    _identity(row, pks[table]) not in authorized.get(table, set()) for row in incoming)
        for row in selected.get(table, []):
            reference = _tuple(row, fk['columns'])
            if all(value is not None for value in reference) and reference not in parent_values:
                issues[(table, 'parent_outside_authorized_scope')] += 1
    for table, pointer, parent, source_owner, target_owner in SEMANTIC:
        if table not in columns or parent not in original or pointer not in available.get(table, set()):
            continue
        if source_owner not in available[table] or target_owner not in available.get(parent, set()):
            issues[(table, 'incomplete_semantic_schema')] += 1
            continue
        targets = {row['id']: row for row in parents[parent]}
        if targets:
            projection = list(dict.fromkeys([*pks[table], pointer, source_owner]))
            incoming = _matching(cursor, table, [pointer], {(ident,) for ident in targets}, projection)
            issues[(table, 'semantic_incoming_outside_authorized_scope')] += sum(
                _identity(row, pks[table]) not in authorized.get(table, set()) for row in incoming)
        for row in selected.get(table, []):
            reference = row.get(pointer)
            if reference is None:
                continue
            target = targets.get(reference)
            if target is None or row[source_owner] != target[target_owner]:
                issues[(table, pointer + '_attempt_mismatch')] += 1
    if any(issues.values()):
        _fail(issues)
    # Only key/reference fields are retained, never names, bodies, grading data,
    # credentials, endpoint URLs, or personal snapshots.
    fields = {table: set(pks[table]) for table in selected}
    for fk in constraints:
        if fk['table'] in fields:
            fields[fk['table']].update(fk['columns'])
        if fk['parent'] in fields:
            fields[fk['parent']].update(fk['parent_columns'])
    for table, pointer, parent, source_owner, target_owner in SEMANTIC:
        if table in fields and pointer in available[table]:
            fields[table].update((pointer, source_owner))
        if parent in fields:
            fields[parent].update(('id', target_owner))
    snapshots = {table: sorted([{field: row[field] for field in sorted(fields[table])} for row in values], key=dumps)
                 for table, values in selected.items()}
    return {'version': 1, 'rows': snapshots,
            'constraints': [fk for fk in constraints if fk['parent'] in selected]}


def verify_manifest_fk_scope(cursor, manifest):
    """Recheck exactly authorized rows immediately before SQL/account deletion."""
    context = manifest.get('fk_ref_context')
    if not isinstance(context, dict) or context.get('version') != 1:
        _fail(Counter({('manifest', 'missing_fk_scope_context'): 1}), 'student_fk_scope_unavailable')
    columns = {}
    for row in rows(cursor, 'SELECT TABLE_NAME,COLUMN_NAME,COLUMN_KEY,ORDINAL_POSITION FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME,ORDINAL_POSITION'):
        columns.setdefault(row['TABLE_NAME'], []).append(row)
    pks = {table: [col['COLUMN_NAME'] for col in fields if col['COLUMN_KEY'] == 'PRI'] for table, fields in columns.items()}
    fks = rows(cursor, FK_QUERY)
    selected, original = {}, context['rows']
    if set(original) != set(manifest['sql']) or set(original) - set(columns):
        _fail(Counter({('manifest', 'changed_fk_scope_tables'): 1}), 'student_fk_scope_unavailable')
    actual = [fk for fk in grouped_foreign_keys(fks) if fk['parent'] in original]
    if actual != context['constraints']:
        _fail(Counter({('manifest', 'changed_fk_constraints'): 1}), 'student_fk_scope_unavailable')
    for table, keys in manifest['sql'].items():
        if (keys and not pks[table]) or any(set(key) != set(pks[table]) for key in keys):
            _fail(Counter({(table, 'changed_primary_key'): 1}), 'student_fk_scope_unavailable')
        # Full rows stay only in memory. check_fk_scope returns structural fields.
        selected[table] = _matching(cursor, table, pks[table], {_tuple(key, pks[table]) for key in keys},
                                    [col['COLUMN_NAME'] for col in columns[table]])
    check_fk_scope(cursor, columns, pks, fks, selected, original=original)
