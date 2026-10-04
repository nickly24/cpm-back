"""Compile current purge code into real-engine SQL tests with synthetic S3 only.

This imports neither application configuration nor the audit's historical code.
The caller owns the empty local MySQL engine and synthetic baseline. Every SQL
statement comes from the current purge/helper functions (parameters are escaped
with the generator's literal function); result assumptions become SQL assertions.
No object-store client is constructed and no network operation is possible here.
"""
import ast
from contextlib import contextmanager
import datetime as dt
from types import SimpleNamespace


def add_purge_checks(sql, checks, assert_sql, denied, gen):
    """Call after the target barrier exists, before manually deleting its rows."""
    root = gen.BACKEND.parent
    helper_path = root / 'cpm-homework-service/homework_service/file_consistency.py'
    helper_nodes = [node for node in ast.parse(helper_path.read_text()).body
                    if isinstance(node, (ast.Assign, ast.FunctionDef, ast.ClassDef))]
    helper = {}
    exec(compile(ast.Module(body=helper_nodes, type_ignores=[]), str(helper_path), 'exec'), helper)
    purge_path = root / 'cpm-homework-service/homework_service/purges.py'
    wanted = {'PurgeBlocked', '_require_barrier', '_check_file_ownership', '_check_manifest',
              '_inventory', '_unlink', '_advance'}
    purge_nodes = [node for node in ast.parse(purge_path.read_text()).body
                   if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in wanted]
    now = dt.datetime(2026, 10, 3, 11, 0, 0)
    job_id = '91111111-1111-4111-8111-111111111111'
    target_file = 'synthetic/one.pdf'
    target_stage = 'synthetic/stage-one.pdf'
    other_file = 'synthetic/two.pdf'
    other_stage = 'synthetic/stage-two.pdf'
    # _advance is internal: run_one_purge normally owns this caller-side fence.
    assert_sql("GET_LOCK('cpm:student-write-fence',30)=1", 'actual_purge_holds_write_fence')
    sql.append('INSERT INTO homework_student_purges(job_id,student_id,created_at,available_at) VALUES(' +
               gen.literal(job_id) + ',101,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))')

    def literal(value):
        if value is None:
            return 'NULL'
        if isinstance(value, dt.datetime):
            return gen.literal(value.isoformat(sep=' '))
        if isinstance(value, (int, float)):
            return str(value)
        return gen.literal(value)

    class Capture:
        def __init__(self):
            self.manifest = {}
            self.pending = None
            self.number = 0

        def label(self, prefix):
            self.number += 1
            return 'actual_purge_' + prefix + '_' + str(self.number)

        def execute(self, query, params=()):
            rendered = query
            for param in params:
                if '%s' not in rendered:
                    raise AssertionError('Too many captured purge SQL parameters')
                rendered = rendered.replace('%s', literal(param), 1)
            if '%s' in rendered:
                raise AssertionError('Unbound captured purge SQL parameter')
            self.pending = rendered
            if query.startswith('SELECT'):
                return
            sql.append(rendered)
            if query.startswith('INSERT IGNORE INTO homework_student_purge_objects'):
                owner, key, kind, deadline = params
                self.manifest[key] = dict(job_id=owner, object_key=key, kind=kind,
                                          not_before=deadline, status='queued', attempts=0,
                                          initial_deleted_at=None, completed_at=None, error_code=None)
            elif query.startswith('UPDATE homework_student_purge_objects SET status='):
                status, completed, _, key = params
                self.manifest[key].update(status=status, completed_at=completed, initial_deleted_at=now)
            elif query.startswith('UPDATE homework_student_purge_objects SET attempts='):
                self.manifest[params[1]]['attempts'] += 1

        def fetchone(self):
            query = self.pending
            if query.startswith('SELECT COUNT(*) count FROM homework_student_purge_objects WHERE '):
                value = sum(row['status'] == 'completed' for row in self.manifest.values()) \
                    if "status='completed'" in query else len(self.manifest)
                assert_sql('(' + query + ')=' + str(value), self.label('receipt_count'))
                return {'count': value}
            if query.startswith(('SELECT COUNT(', 'SELECT (SELECT COUNT(')):
                assert_sql('(' + query + ')=0', self.label('ownership_count'))
                return {'count': 0}
            if query.startswith('SELECT 1 FROM homework_object_ownership'):
                assert_sql('NOT EXISTS (' + query + ')', self.label('exact_key_conflict_absent'))
                return None
            if query.startswith('SELECT job_id,created_at FROM student_deletion_barriers'):
                sql.append(query)  # Preserve the actual FOR UPDATE statement.
                assert_sql('(SELECT COUNT(*) FROM student_deletion_barriers WHERE student_id=101 AND '
                           'CAST(job_id AS BINARY)=CAST(' + literal(job_id) + ' AS BINARY))=1',
                           self.label('barrier_matches'))
                return {'job_id': job_id, 'created_at': now}
            if query.startswith('SELECT * FROM homework_student_purge_objects'):
                sql.append(query)  # Original filtered/ordered/locking SQL, unchanged.
                pending = sorted((row for row in self.manifest.values() if row['status'] != 'completed'),
                                 key=lambda row: row['object_key'])
                row = pending[0] if pending else None
                condition = '(SELECT COUNT(*) FROM homework_student_purge_objects WHERE '
                condition += 'CAST(job_id AS BINARY)=CAST(' + literal(job_id) + ' AS BINARY) '
                if row:
                    condition += 'AND CAST(object_key AS BINARY)=CAST(' + literal(row['object_key'])
                    condition += " AS BINARY) AND status<>'completed')=1"
                else:
                    condition += "AND status<>'completed')=0"
                assert_sql(condition, self.label('selected_manifest_row'))
                return dict(row) if row else None
            if query.startswith('SELECT MIN(not_before) wake_at FROM homework_student_purge_objects'):
                assert_sql('(' + query + ') IS NULL', self.label('no_unfinished_deadline'))
                return {'wake_at': None}
            raise AssertionError('Unsupported captured purge read: ' + query)

        def fetchall(self):
            query = self.pending
            if query.startswith('SELECT object_key,kind,writable_until FROM homework_object_ownership'):
                result = [{'object_key': target_file, 'kind': 'processed', 'writable_until': None}]
            elif query.startswith('SELECT f.object_key FROM homework_submission_files'):
                result = [{'object_key': target_file}]
            elif query.startswith('SELECT staging_key,processed_output_key,upload_expires_at FROM homework_file_jobs'):
                result = [{'staging_key': target_stage, 'processed_output_key': target_file,
                           'upload_expires_at': None}]
            else:
                raise AssertionError('Unsupported captured purge inventory: ' + query)
            sql.append(query)
            assert_sql('(SELECT COUNT(*) FROM (' + query + ') expected_inventory)=' + str(len(result)),
                       self.label('inventory_rows'))
            for row in result:
                for field, value in row.items():
                    # Timestamp values drive the synthetic drain clock only;
                    # baseline types/defaults are independently engine-checked.
                    if value is not None:
                        assert_sql('(SELECT COUNT(*) FROM (' + query + ') expected_inventory WHERE '
                                   'CAST(' + field + ' AS BINARY)=CAST(' + literal(value) + ' AS BINARY))=1',
                                   self.label('inventory_value'))
            return result

    cursor = Capture()

    @contextmanager
    def transaction():
        sql.append('START TRANSACTION')
        yield None, cursor
        sql.append('COMMIT')

    class SyntheticStorage:
        def __init__(self):
            self.objects = {target_file, target_stage, other_file, other_stage}
            self.deleted = []

        def delete(self, key):
            self.deleted.append(key)
            self.objects.discard(key)

        def verify_absent(self, key):
            if key in self.objects:
                raise AssertionError('Synthetic object remains')

    storage = SyntheticStorage()
    namespace = {
        'dt': dt, 'utcnow': lambda: now, 'db': SimpleNamespace(transaction=transaction),
        'KEY_OWNERS_SQL': helper['KEY_OWNERS_SQL'], 'FileConsistencyError': helper['FileConsistencyError'],
        # Exact schema columns were already validated against the actual engine.
        'check_file_consistency': lambda cur, student: helper['check_file_consistency'](
            cur, student, helper['REQUIRED_COLUMNS']),
    }
    exec(compile(ast.Module(body=purge_nodes, type_ignores=[]), str(purge_path), 'exec'), namespace)
    app = SimpleNamespace(config={'STUDENT_PURGE_STORAGE_POLICY_VERIFIED': True,
                                  'LEGACY_PRESIGN_MAX_SECONDS': 0, 'PRESIGN_DRAIN_SECONDS': 0},
                          extensions={'homework_storage': storage})
    purge = dict(job_id=job_id, student_id=101, inventory_completed_at=None,
                 sql_unlinked_at=None, total_units=0)
    namespace['_advance'](app, purge)
    assert_sql('(SELECT COUNT(*) FROM homework_submissions WHERE student_id=101)=1', 'actual_purge_manifest_before_unlink')
    purge.update(inventory_completed_at=now, total_units=3)
    namespace['_advance'](app, purge)
    purge['sql_unlinked_at'] = now
    assert_sql('(SELECT COUNT(*) FROM homework_submissions WHERE student_id=101)=0', 'actual_purge_unlinked_target')
    assert_sql('(SELECT COUNT(*) FROM homework_submissions WHERE student_id=202)=1', 'actual_purge_kept_other_submission')
    namespace['_advance'](app, purge)
    namespace['_advance'](app, purge)
    namespace['_advance'](app, purge)
    if storage.deleted != [target_file, target_stage] or storage.objects != {other_file, other_stage}:
        raise AssertionError('Synthetic purge storage scope was not exact')
    assert_sql("(SELECT COUNT(*) FROM homework_student_purges WHERE job_id=" + literal(job_id) +
               " AND status='completed' AND total_units=3 AND completed_units=3)=1", 'actual_purge_completed_after_receipts')
    assert_sql("(SELECT COUNT(*) FROM homework_student_purge_objects WHERE job_id=" + literal(job_id) +
               " AND status='completed' AND completed_at IS NOT NULL)=2", 'actual_purge_retained_completed_receipts')
    assert_sql("(SELECT COUNT(*) FROM homework_submission_files WHERE id=22 AND submission_id=2002)=1",
               'actual_purge_kept_other_file')
    assert_sql("(SELECT COUNT(*) FROM homework WHERE id=1)=1", 'actual_purge_kept_shared_homework')
    assert_sql("RELEASE_LOCK('cpm:student-write-fence')=1", 'actual_purge_releases_write_fence')
    checks.append('actual_purge_storage_stub_exact_keys_only_no_network')


def add_workflow_file_checks(sql, checks, assert_sql, denied, gen):
    """Exercise current producer SQL with strict OLD+NEW guards, before barrier.

    Each synthetic scenario is rolled back so the caller's baseline is intact.
    Authentication and reads return declared fixture rows; every mutation and
    post-action aggregate ownership query is executed by the real SQL engine.
    """
    root = gen.BACKEND.parent / 'cpm-homework-service/homework_service'
    helper_nodes = [node for node in ast.parse((root / 'file_consistency.py').read_text()).body
                    if isinstance(node, (ast.Assign, ast.FunctionDef, ast.ClassDef))]
    helper = {}
    exec(compile(ast.Module(body=helper_nodes, type_ignores=[]), str(root / 'file_consistency.py'), 'exec'), helper)
    tree = ast.parse((root / 'workflow.py').read_text())
    workflow_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'HomeworkWorkflow')
    workflow_class.body = [node for node in workflow_class.body if isinstance(node, ast.FunctionDef)
                           and node.name in {'__init__', 'submit', 'remove_draft', 'transition'}]
    workflow_nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                      and node.name in {'_iso', 'WorkflowError'}] + [workflow_class]
    finish_tree = ast.parse((root / 'jobs.py').read_text())
    finish_nodes = [node for node in finish_tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                    and node.name in {'JobCancelled', '_finish_job'}]
    class InjectDependencies(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            # The workflow class is injected below, without package imports.
            return None
    finish_nodes = [InjectDependencies().visit(node) for node in finish_nodes]
    outbox_node = next(node for node in ast.parse((root / 'outbox.py').read_text()).body
                       if isinstance(node, ast.FunctionDef) and node.name == 'enqueue_delete')
    target_job = '11111111-1111-4111-8111-111111111111'
    historical_job = '44444444-4444-4444-8444-444444444444'

    class SqlVariable:
        name = '@cpm_workflow_new_file_id'

    def literal(value):
        if isinstance(value, SqlVariable):
            return value.name
        if value is None:
            return 'NULL'
        if isinstance(value, dt.datetime):
            return gen.literal(value.isoformat(sep=' '))
        if isinstance(value, (int, float)):
            return str(value)
        return gen.literal(value)

    for action in ('submit', 'remove_draft', 'resubmit', 'finish_job'):
        prefix = 'actual_workflow_' + action + '_'
        assert_sql("GET_LOCK('cpm:student-write-fence',30)=1", prefix + 'fence')
        sql.append('START TRANSACTION')
        sql.append("INSERT INTO homework_submission_files(id,submission_id,object_key,status,size_bytes,page_count,sha256) VALUES(33,1001,'synthetic/draft-one.pdf','draft',10,1,REPEAT('3',64))")
        state = 'graded' if action == 'resubmit' else 'revision_requested'
        sql.append("UPDATE homework_submissions SET state=" + literal(state) + ',draft_file_id=33 WHERE id=1001')
        result_id = 11 if action == 'submit' else 33
        job_status = 'running' if action == 'finish_job' else 'ready'
        sql.append('UPDATE homework_file_jobs SET status=' + literal(job_status) + ',result_file_id=' +
                   str(result_id) + ' WHERE id=' + literal(target_job))
        sql.append('INSERT INTO homework_file_jobs(id,client_upload_id,submission_id,homework_id,student_id,status,result_file_id) VALUES(' +
                   literal(historical_job) + ",'54444444-4444-4444-8444-444444444444',1001,1,101,'ready'," + str(result_id) + ')')
        if action == 'finish_job':
            sql.append("INSERT INTO homework_object_ownership(object_key,student_id,submission_id,upload_job_id,kind) VALUES('synthetic/new-draft-one.pdf',101,1001," + literal(target_job) + ",'processed')")
            sql.append("UPDATE homework_file_jobs SET processed_output_key='synthetic/new-draft-one.pdf' WHERE id=" + literal(target_job))
        sub = dict(id=1001, homework_id=1, student_id=101, state=state, current_file_id=11,
                   draft_file_id=33, submitted_at_utc=None, reviewer_role=None, reviewer_id=None)
        job = dict(id=target_job, submission_id=1001, homework_id=1, student_id=101, status=job_status)

        class Capture:
            lastrowid = SqlVariable()

            def __init__(self):
                self.query = None
                self.params = ()
                self.number = 0

            def label(self, kind):
                self.number += 1
                return prefix + kind + '_' + str(self.number)

            def execute(self, query, params=()):
                self.params = params
                rendered = query
                for param in params:
                    if '%s' not in rendered:
                        raise AssertionError('Too many workflow SQL parameters')
                    rendered = rendered.replace('%s', literal(param), 1)
                if '%s' in rendered:
                    raise AssertionError('Unbound workflow SQL parameter')
                self.query = rendered
                if query.startswith('SELECT COUNT('):
                    return
                sql.append(rendered)
                if query.startswith('INSERT INTO homework_submission_files '):
                    sql.append('SET @cpm_workflow_new_file_id=LAST_INSERT_ID()')

            def fetchone(self):
                if self.query.startswith('SELECT COUNT('):
                    assert_sql('(' + self.query + ')=0', self.label('ownership_clean'))
                    return {'count': 0}
                if self.query.startswith('SELECT * FROM homework_submissions'):
                    return dict(sub)
                if self.query.startswith('SELECT * FROM homework_file_jobs'):
                    return dict(job)
                if self.query.startswith('SELECT object_key FROM homework_submission_files WHERE id='):
                    key = 'synthetic/one.pdf' if self.params[0] == 11 else 'synthetic/draft-one.pdf'
                    return {'object_key': key}
                raise AssertionError('Unsupported captured workflow read: ' + self.query)

            def fetchall(self):
                if self.query.startswith('SELECT object_key FROM homework_submission_files WHERE submission_id='):
                    return [{'object_key': 'synthetic/one.pdf'}, {'object_key': 'synthetic/draft-one.pdf'}]
                raise AssertionError('Unsupported captured workflow list: ' + self.query)

        cursor = Capture()

        @contextmanager
        def transaction():
            yield None, cursor

        namespace = dict(dt=dt, OMITTED=object(), EDITABLE_STATES={'none', 'uploading', 'processing', 'draft', 'revision_requested'},
                         db=SimpleNamespace(transaction=transaction, student_is_blocked=lambda *args: False,
                                            deletion_protection_active=lambda: True))
        exec(compile(ast.Module(body=[outbox_node], type_ignores=[]), str(root / 'outbox.py'), 'exec'), namespace)
        exec(compile(ast.Module(body=workflow_nodes, type_ignores=[]), str(root / 'workflow.py'), 'exec'), namespace)
        workflow = namespace['HomeworkWorkflow']({}, None)
        workflow._identity = lambda *args: None
        workflow._homework = lambda *args, **kwargs: None
        workflow._submission = lambda *args, **kwargs: dict(sub)
        workflow._editable = lambda *args: None
        workflow._active_job = lambda *args, **kwargs: None
        workflow._access_student = lambda *args: None
        namespace['HomeworkWorkflow']._legacy_graded = staticmethod(lambda *args: False)
        if action == 'finish_job':
            exec(compile(ast.Module(body=finish_nodes, type_ignores=[]), str(root / 'jobs.py'), 'exec'), namespace)
            namespace['_finish_job'](job, 'synthetic/new-draft-one.pdf', dict(size_bytes=10, page_count=1, sha256='4' * 64))
            assert_sql('(SELECT COUNT(*) FROM homework_submissions WHERE id=1001 AND current_file_id=11 AND '
                       'draft_file_id=@cpm_workflow_new_file_id)=1', prefix + 'new_draft_reference')
            assert_sql('(SELECT COUNT(*) FROM homework_file_jobs WHERE id=' + literal(target_job) +
                       ' AND result_file_id=@cpm_workflow_new_file_id)=1', prefix + 'new_job_reference')
        elif action == 'resubmit':
            workflow.transition({'role': 'proctor', 'id': 101}, 1001, 'resubmit')
            assert_sql('(SELECT COUNT(*) FROM homework_submission_files WHERE submission_id=1001)=0', prefix + 'no_file_rows')
        else:
            getattr(workflow, action)({'role': 'student', 'id': 101}, 1)
            remaining_file = 33 if action == 'submit' else 11
            assert_sql('(SELECT COUNT(*) FROM homework_submission_files WHERE submission_id=1001 AND id=' +
                       str(remaining_file) + ')=1', prefix + 'expected_file_remains')
        assert_sql('(SELECT COUNT(*) FROM homework_file_jobs WHERE id=' + literal(historical_job) +
                   ' AND result_file_id IS NULL)=1', prefix + 'old_result_cleared')
        helper['check_file_consistency'](cursor, 101, helper['REQUIRED_COLUMNS'])
        assert_sql('(SELECT COUNT(*) FROM homework_submission_files WHERE id=22 AND submission_id=2002)=1', prefix + 'other_student_preserved')
        sql.append('ROLLBACK')
        assert_sql("RELEASE_LOCK('cpm:student-write-fence')=1", prefix + 'release_fence')
        assert_sql('(SELECT COUNT(*) FROM homework_submission_files WHERE id=11 AND submission_id=1001)=1', prefix + 'baseline_restored')
