"""Offline synthetic fences: no production app, sockets, SQL or Mongo access."""
import threading
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask

from cpm_back.auth.jwt_auth import generate_token, verify_token
from cpm_back.services.student_deletion import fence


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.barriers = set()
        self.students = {7, 8}
        self.accounts = {7, 8}
        self.events = []
        self.schema_installed = True


class Connection:
    in_transaction = False

    def __init__(self, store):
        self.store = store
        self.closed = False

    def cursor(self, **kwargs):
        return Cursor(self)

    def rollback(self):
        self.store.events.append('rollback')

    def close(self):
        self.closed = True


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = None

    def execute(self, sql, params=()):
        store = self.connection.store
        if 'GET_LOCK' in sql:
            self.result = (int(store.lock.acquire(timeout=params[1])),)
            store.events.append('acquire')
        elif 'RELEASE_LOCK' in sql:
            store.lock.release()
            self.result = (1,)
            store.events.append('release')
        elif 'FROM students s' in sql:
            ident = params[0]
            allowed = ident in store.students and ident in store.accounts
            if 'student_deletion_barriers' in sql:
                allowed = allowed and ident not in store.barriers
            self.result = {'id': ident, 'full_name': 'Synthetic', 'group_id': 2} if allowed else None
        elif sql == 'SELECT 1 FROM student_deletion_barriers LIMIT 0':
            if not store.schema_installed:
                from mysql.connector.errors import ProgrammingError
                raise ProgrammingError('synthetic missing table', errno=1146)
            self.result = None
        elif 'FROM student_deletion_barriers' in sql:
            self.result = {'student_id': params[0]} if params[0] in store.barriers else None
        elif 'FROM students' in sql:
            self.result = {'id': params[0]} if params[0] in store.students else None
        else:
            raise AssertionError('Unexpected synthetic SQL: ' + sql)

    def fetchone(self):
        return self.result

    def close(self):
        pass


class MongoCollection:
    def __init__(self, name='test_attempts', docs=None):
        self.name = name
        self.docs = docs or []
        self.write_concern = SimpleNamespace(acknowledged=True)
        self.writes = []

    def find(self, query, projection):
        return [doc for doc in self.docs if all(doc.get(k) == v for k, v in query.items())]

    def __getattr__(self, method):
        if method in fence._MONGO_WRITES:
            def operation(*args, **kwargs):
                self.writes.append((method, args, kwargs))
                return SimpleNamespace(acknowledged=True)
            return operation
        raise AttributeError(method)


class FenceTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, STUDENT_DELETION_ENABLED=True,
                               JWT_SECRET_KEY='synthetic-student-erasure-offline-secret')
        self.store = Store()
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        patches = [
            patch.object(fence, '_barrier_schema_seen', False),
            patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')),
            patch('mysql.connector.connect', side_effect=AssertionError('SQL forbidden')),
            patch.object(fence, '_connection', side_effect=lambda: Connection(self.store)),
            patch.object(fence, '_close', side_effect=lambda connection: connection.close()),
            patch.object(fence, 'verify_write_barrier_schema', return_value={'guard_version': 1}),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def token(self):
        return generate_token({'role': 'student', 'id': 7, 'full_name': 'Old name', 'group_id': 999})

    def test_old_token_uses_live_account_and_current_profile(self):
        token = self.token()
        self.assertEqual(verify_token(token)['group_id'], 2)
        self.store.accounts.remove(7)
        self.assertIsNone(verify_token(token))
        self.store.accounts.add(7)
        self.store.students.remove(7)
        self.assertIsNone(verify_token(token))

    def test_pending_and_completed_barrier_revokes_old_token_forever(self):
        token = self.token()
        self.store.barriers.add(7)
        self.assertIsNone(verify_token(token))
        self.store.students.discard(7)
        self.assertIsNone(verify_token(token))
        self.store.students.add(7)  # Even accidental restoration cannot revive JWT.
        self.assertIsNone(verify_token(token))

    def test_student_account_check_remains_on_before_feature_enable(self):
        self.app.config['STUDENT_DELETION_ENABLED'] = False
        self.store.accounts.remove(7)
        self.assertIsNone(verify_token(self.token()))

    def test_missing_barrier_schema_denies_auth_and_mutation(self):
        with patch.object(fence, '_connection', side_effect=RuntimeError('missing table')):
            self.assertIsNone(verify_token(self.token()))
        with patch.object(fence, 'verify_write_barrier_schema', side_effect=fence.StudentFenceUnavailable('missing_trigger')):
            with self.assertRaises(fence.StudentFenceUnavailable):
                with fence.mutation_fence():
                    self.fail('Missing guards must not permit writes')
        self.assertFalse(self.store.lock.locked())

    def test_nested_fences_use_one_connection_and_release_on_error(self):
        with self.assertRaisesRegex(ValueError, 'synthetic'):
            with fence.mutation_fence((7,)) as outer:
                with fence.mutation_fence((8,)) as inner:
                    self.assertIs(outer, inner)
                    raise ValueError('synthetic')
        self.assertEqual(self.store.events.count('acquire'), 1)
        self.assertEqual(self.store.events.count('release'), 1)
        self.assertFalse(self.store.lock.locked())

    def test_domain_transaction_cannot_take_fence_after_row_locks(self):
        connection = Connection(self.store)
        connection.in_transaction = True
        with self.assertRaisesRegex(fence.StudentFenceUnavailable, 'idle_connection'):
            with fence.mutation_fence(connection=connection):
                pass
        self.assertFalse(self.store.lock.locked())

    def test_fence_drains_inflight_writer_then_blocks_late_writer(self):
        entered = threading.Event()
        finish = threading.Event()
        barrier_committed = threading.Event()
        failures = []

        def writer():
            with self.app.app_context(), fence.mutation_fence((7,)):
                entered.set()
                finish.wait(3)
                self.store.events.append('writer_committed')

        def deleter():
            with self.app.app_context(), fence.mutation_fence():
                self.store.barriers.add(7)
                self.store.events.append('barrier_committed')
                barrier_committed.set()

        first = threading.Thread(target=writer)
        second = threading.Thread(target=deleter)
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        self.assertFalse(barrier_committed.wait(0.05))
        finish.set()
        first.join(3)
        second.join(3)
        self.assertTrue(barrier_committed.is_set())
        self.assertLess(self.store.events.index('writer_committed'), self.store.events.index('barrier_committed'))
        with self.assertRaises(fence.StudentWriteBlocked):
            with fence.mutation_fence((7,)):
                failures.append('late_write')
        self.assertFalse(failures)

    def test_staff_direct_mongo_id_update_checks_matched_owner(self):
        raw = MongoCollection(docs=[{'_id': 'attempt-7', 'studentId': 7}])
        guarded = fence.GuardedMongoCollection(raw)
        self.store.barriers.add(7)
        with self.assertRaises(fence.StudentWriteBlocked):
            guarded.update_one({'_id': 'attempt-7'}, {'$set': {'answer': 'late'}})
        self.assertFalse(raw.writes)

    def test_late_rating_backfill_and_upsert_cannot_recreate_student(self):
        raw = MongoCollection('rate_rec')
        guarded = fence.GuardedMongoCollection(raw)
        self.store.barriers.add(7)
        self.store.students.remove(7)
        with self.assertRaises(fence.StudentWriteBlocked):
            guarded.insert_one({'student_id': 7, 'details': []})
        with self.assertRaises(fence.StudentWriteBlocked):
            guarded.update_one({'rating_id': 55}, {'$set': {'student_id': '7'}}, upsert=True)
        self.assertFalse(raw.writes)

    def test_ownership_removal_pipeline_bulk_and_unacknowledged_writes_denied(self):
        raw = MongoCollection(docs=[{'_id': 'a', 'studentId': 7}])
        guarded = fence.GuardedMongoCollection(raw)
        for update in ({'$unset': {'studentId': 1}}, [{'$set': {'answer': 'x'}}], {'$rename': {'studentId': 'other'}}):
            with self.assertRaises(fence.StudentWriteBlocked):
                guarded.update_one({'_id': 'a'}, update)
        with self.assertRaises(fence.StudentWriteBlocked):
            guarded.bulk_write([])
        raw.write_concern.acknowledged = False
        with self.assertRaises(fence.StudentFenceUnavailable):
            guarded.insert_one({'studentId': 7})
        self.assertFalse(raw.writes)

    def test_deletion_remains_allowed_and_shared_definitions_are_preserved(self):
        raw = MongoCollection()
        self.store.barriers.add(7)
        fence.GuardedMongoCollection(raw).delete_many({'studentId': {'$in': [7, '7']}})
        self.assertEqual(raw.writes[0][0], 'delete_many')
        shared = MongoCollection('tests')
        fence.GuardedMongoCollection(shared).insert_one({'title': 'Shared definition'})
        self.assertEqual(len(shared.writes), 1)
        unknown = MongoCollection('unknown_personal_collection')
        with self.assertRaises(fence.StudentWriteBlocked):
            fence.GuardedMongoCollection(unknown).insert_one({'studentId': 7})

    def test_http_get_mutation_and_staff_nested_payload_are_fenced(self):
        fence.register_request_hooks(self.app)
        called = []
        self.app.add_url_rule('/staff', 'staff', lambda: called.append(True) or {'ok': True}, methods=['GET', 'POST'])
        self.store.barriers.add(7)
        client = self.app.test_client()
        response = client.post('/staff', json={'rows': [{'student_id': 7}]})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(called)
        self.assertFalse(self.store.lock.locked())
        response = client.get('/staff')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.events.count('acquire'), 2)
        self.assertFalse(self.store.lock.locked())

    def test_health_preflight_and_unknown_route_do_not_probe_database(self):
        fence.register_request_hooks(self.app)
        self.app.add_url_rule('/', 'health', lambda: {'ok': True})
        self.app.add_url_rule('/api/synthetic', 'synthetic', lambda: {'ok': True})
        with patch.object(fence, 'protection_active', side_effect=AssertionError('read-only boundary probed')) as probe:
            client = self.app.test_client()
            self.assertEqual(client.get('/').status_code, 200)
            self.assertEqual(client.open('/api/synthetic', method='OPTIONS').status_code, 200)
            self.assertEqual(client.get('/missing-route').status_code, 404)
            probe.assert_not_called()

    def test_flag_false_retains_existing_tombstones_for_auth_mongo_and_workers(self):
        self.app.config['STUDENT_DELETION_ENABLED'] = False
        self.store.barriers.add(7)
        self.assertIsNone(verify_token(self.token()))
        raw = MongoCollection()
        with self.assertRaises(fence.StudentWriteBlocked):
            fence.GuardedMongoCollection(raw).insert_one({'studentId': 7})
        @fence.fenced_mutation
        def late_worker():
            fence.check_student_writable(7)
        with self.assertRaises(fence.StudentWriteBlocked):
            late_worker()
        self.assertFalse(raw.writes)

    def test_absent_schema_is_legacy_only_until_first_positive_probe(self):
        self.app.config['STUDENT_DELETION_ENABLED'] = False
        self.store.schema_installed = False
        self.assertFalse(fence.protection_active())
        self.assertFalse(fence._barrier_schema_seen)
        self.store.schema_installed = True
        self.assertTrue(fence.protection_active())
        self.assertTrue(fence._barrier_schema_seen)
        self.store.schema_installed = False
        self.assertTrue(fence.protection_active())
        with patch.object(fence, 'verify_write_barrier_schema', side_effect=fence.StudentFenceUnavailable('dropped schema')):
            with self.assertRaises(fence.StudentFenceUnavailable):
                with fence.mutation_fence():
                    self.fail('Cannot return to legacy after installed schema')

    def test_disabled_flag_never_swallows_probe_errors(self):
        self.app.config['STUDENT_DELETION_ENABLED'] = False
        with patch.object(fence, '_connection', side_effect=RuntimeError('synthetic outage')):
            with self.assertRaises(fence.StudentFenceUnavailable):
                fence.protection_active()

    def test_current_rating_worker_fence_timeout_records_terminal_unclaimed_failure(self):
        from cpm_back.services.exams import rating_jobs
        with patch.object(rating_jobs, 'mutation_fence', side_effect=fence.StudentFenceUnavailable('busy')), patch.object(rating_jobs, '_run') as run, patch.object(rating_jobs, '_fail_unclaimed_job') as fail:
            rating_jobs.run(123)
            run.assert_not_called()
            fail.assert_called_once_with(123)
        db = MagicMock()
        db.one.return_value = {'active_job_id': 123}
        db.cursor.rowcount = 1
        @contextmanager
        def transaction(**kwargs):
            yield db
        with patch.object(rating_jobs, 'transaction', transaction):
            rating_jobs._fail_unclaimed_job(123)
            self.assertIn("status='queued'", db.execute.call_args.args[0])
            db.update.assert_called_once_with('rating_source_state', {'active_job_id': None}, 'id=1', ())
            db.update.reset_mock()
            db.cursor.rowcount = 0  # A duplicate worker has already claimed it.
            rating_jobs._fail_unclaimed_job(123)
            db.update.assert_not_called()

    def test_import_and_rating_fence_timeout_clear_gate_and_allow_next_enqueue(self):
        from cpm_back.services.user_import import import_jobs
        from cpm_back.services.exam import rating_recalc_jobs
        for module, worker, active, enqueue in (
            (import_jobs, '_run_import_job', '_active_import_job_id', 'enqueue_import_job'),
            (rating_recalc_jobs, '_run_recalc_job', '_active_recalc_job_id', 'enqueue_recalc_job'),
        ):
            with self.subTest(worker=worker), patch.object(module, '_update_job') as update, patch.object(module, 'mutation_fence', side_effect=fence.StudentFenceUnavailable('busy')):
                setattr(module, active, 123)
                getattr(module, worker)(123)
                self.assertIsNone(getattr(module, active))
                update.assert_called_once()
                self.assertEqual(update.call_args.kwargs['status'], 'failed')
                with patch.object(module.threading, 'Thread') as thread, patch('cpm_back.services.exams.rating.enabled', return_value=False):
                    getattr(module, enqueue)(124)
                    thread.return_value.start.assert_called_once()
                    self.assertEqual(getattr(module, active), 124)
                setattr(module, active, None)

    def test_background_import_unit_is_fenced_before_any_work(self):
        @fence.fenced_mutation
        def imported():
            self.assertTrue(self.store.lock.locked())
            fence.check_student_writable(7)
        self.store.barriers.add(7)
        with self.assertRaises(fence.StudentWriteBlocked):
            imported()


class SchemaManifestTests(unittest.TestCase):
    def schema(self):
        columns = {'students': {'id': 'int'}, 'auth_users': {'role': 'enum', 'ref_id': 'int'},
                   'student_deletion_barriers': {'student_id': 'bigint', 'job_id': 'char', 'created_at': 'datetime'},
                   'student_deletion_guard_state': {'id': 'tinyint', 'schema_version': 'int'}}
        return columns, [], {table: 'InnoDB' for table in columns}

    def test_sql_normalization_preserves_security_significant_literals(self):
        self.assertEqual(fence._normalize_sql("SELECT  `student_id`\n FROM `students`"), 'SELECT student_id FROM students')
        original = "IF BINARY role='student' AND value='a  `B`' THEN"
        self.assertNotEqual(fence._normalize_sql(original), fence._normalize_sql(original.replace("'student'", "'Student'")))
        self.assertNotEqual(fence._normalize_sql(original), fence._normalize_sql(original.replace('a  `B`', 'a `B`')))
        self.assertNotEqual(fence._normalize_sql('SELECT id FROM Allratings'), fence._normalize_sql('SELECT id FROM allratings'))

    def test_missing_minimum_and_unknown_student_schema_fail_closed(self):
        columns, refs, engines = self.schema()
        self.assertIn('students', fence._validate_ownership_schema(columns, refs, engines))
        columns['unreviewed'] = {'student_id': 'int'}
        with self.assertRaisesRegex(fence.StudentFenceUnavailable, 'unknown_student_ownership_table'):
            fence._validate_ownership_schema(columns, refs, engines)
        columns.pop('unreviewed')
        columns.pop('student_deletion_barriers')
        with self.assertRaisesRegex(fence.StudentFenceUnavailable, 'missing_student_write_guard'):
            fence._validate_ownership_schema(columns, refs, engines)

    def test_new_ownership_column_on_known_table_fails_closed(self):
        for key in fence._ID_KEYS | fence._IDS_KEYS:
            with self.subTest(key=key):
                columns, refs, engines = self.schema()
                columns['students'][key] = 'int'
                with self.assertRaisesRegex(fence.StudentFenceUnavailable, 'unknown_student_ownership_column'):
                    fence._validate_ownership_schema(columns, refs, engines)

    def test_unknown_indirect_reference_fails_closed(self):
        columns, refs, engines = self.schema()
        refs.append({'TABLE_NAME': 'unreviewed_child', 'REFERENCED_TABLE_NAME': 'students'})
        with self.assertRaisesRegex(fence.StudentFenceUnavailable, 'unknown_student_ownership_reference'):
            fence._validate_ownership_schema(columns, refs, engines)

    def test_all_known_tables_have_insert_and_old_new_update_guards(self):
        tables = set(fence.DIRECT_TABLES) | set(fence.INDIRECT_TABLES) | fence.SPECIAL_TABLES
        definitions = list(fence._trigger_definitions(tables))
        self.assertEqual(len(definitions), len(tables) * 2 + 1)
        for name, table, event, timing, body in definitions:
            self.assertIn('cpm_assert_student_', body)
            if event == 'DELETE':
                self.assertEqual(table, 'exam_admin_commands')
                self.assertIn('student_command_tombstone_immutable', body)
                continue
            self.assertIn('NEW.', body)
            if event == 'UPDATE':
                self.assertIn('OLD.', body)
            if table == 'students' and event == 'INSERT':
                self.assertEqual(timing, 'AFTER')
        self.assertTrue({'student_card_progress', 'student_section_study_settings'} <= tables)
        self.assertFalse({'tests', 'exams', 'classic_exam_questions'} & tables)

    def test_journal_guards_check_new_documents_and_permit_redaction(self):
        for _, table, _, _, body in fence._trigger_definitions(set(fence.JOURNAL_COLUMNS) - fence.SPECIAL_TABLES):
            self.assertIn('cpm_assert_student_json_write(NEW.', body)
            self.assertNotIn('OLD.', body)
        json_guard = next(s for s in fence._migration_statements() if s.startswith('CREATE PROCEDURE cpm_assert_student_json_write'))
        for key in fence._ID_KEYS | fence._IDS_KEYS | {'student.id'}:
            self.assertIn('$**.' + key, json_guard)
        self.assertIn('CALL cpm_assert_student_write(NULL)', json_guard)
        self.assertIn('CALL cpm_assert_student_journal_id', json_guard)

    def test_sql_barrier_drain_uses_transaction_locks_and_immutable_tombstones(self):
        statements = fence._migration_statements()
        routine = next(s for s in statements if s.startswith('CREATE PROCEDURE'))
        insert = next(s for s in statements if s.startswith('CREATE TRIGGER cpm_sdb_insert'))
        self.assertIn('LOCK IN SHARE MODE', routine)
        self.assertIn('FOR UPDATE', insert)
        self.assertEqual(sum('student_deletion_barrier_immutable' in s for s in statements), 2)
        self.assertNotIn('DELETE FROM student_deletion_barriers', '\n'.join(statements))

    def test_retired_personal_tables_have_both_old_and_new_guards(self):
        definitions = list(fence._trigger_definitions(set(fence.LEGACY_GUARD_COLUMNS)))
        self.assertEqual(len(definitions), 16)
        for _, table, event, _, body in definitions:
            self.assertIn('NEW.', body)
            if event == 'UPDATE':
                self.assertIn('OLD.', body)
        messages = fence._owner_checks('homework_chat_messages', 'NEW')
        self.assertIn('NEW.sender_role,NEW.sender_id,owner_id', messages)
        reads = fence._owner_checks('homework_chat_reads', 'OLD')
        self.assertIn('OLD.last_message_id<>0', reads)
        self.assertIn('OLD.last_message_id,OLD.thread_id,owner_id', reads)
        followers = fence._owner_checks('homework_chat_admin_followers', 'NEW')
        self.assertNotIn('NEW.admin_id', followers)
        self.assertIn('LOCK IN SHARE MODE', followers)

    def test_retired_ownership_columns_and_dependencies_fail_closed(self):
        columns, refs, engines = self.schema()
        for table, fields in fence.LEGACY_GUARD_COLUMNS.items():
            columns[table] = {field: 'int' for field in fields}
            engines[table] = 'InnoDB'
        for table, fields in fence.HOMEWORK_GUARD_COLUMNS.items():
            columns[table] = {field: 'int' for field in fields}
            engines[table] = 'InnoDB'
        self.assertTrue(set(fence.LEGACY_GUARD_COLUMNS) <= fence._validate_ownership_schema(columns, refs, engines))
        columns['homework_chat_reads'].pop('last_message_id')
        with self.assertRaisesRegex(fence.StudentFenceUnavailable, 'schema_mismatch:homework_chat_reads'):
            fence._validate_ownership_schema(columns, refs, engines)
        columns['homework_chat_reads']['last_message_id'] = 'bigint'
        columns.pop('homework_chat_messages')
        with self.assertRaisesRegex(fence.StudentFenceUnavailable, 'missing_reference'):
            fence._validate_ownership_schema(columns, refs, engines)

    def test_role_guard_preserves_staff_id_collisions_and_checks_student_conflicts(self):
        routine = next(s for s in fence._migration_statements() if s.startswith('CREATE PROCEDURE cpm_assert_student_role_write('))
        self.assertIn("IF BINARY p_role='student' THEN", routine)
        self.assertIn('p_owner<>p_id', routine)
        self.assertIn('student_write_owner_conflict', routine)
        self.assertNotIn("p_role='admin' THEN CALL", routine)

    def test_outbox_guard_only_resolves_documented_typed_variants(self):
        routine = next(s for s in fence._migration_statements() if s.startswith('CREATE PROCEDURE cpm_assert_student_outbox_write('))
        for event in ('message.created', 'message.read', 'typing.changed', 'submission.changed', 'job.progress', 'notification.created'):
            self.assertIn("'" + event + "'", routine)
        self.assertIn('student_write_outbox_event_unknown', routine)
        self.assertIn('JSON_LENGTH(document)<>2', routine)
        self.assertIn('NOT (p_entity<=>payload_id)', routine)
        self.assertIn('FROM notifications WHERE id=payload_id LOCK IN SHARE MODE', routine)
        self.assertIn('FROM homework_file_jobs j JOIN homework_submissions s', routine)
        self.assertIn('WHERE BINARY j.id=BINARY job_id LOCK IN SHARE MODE', routine)
        self.assertNotIn('cpm_assert_student_write(p_entity)', routine)
        self.assertNotIn('cpm_assert_student_write(room_id)', routine)

    def test_command_erasure_is_exact_monotonic_and_durably_authorized(self):
        definitions = {event: body for _, _, event, _, body in fence._trigger_definitions({'exam_admin_commands'})}
        update = definitions['UPDATE']
        for key in ('id', 'actor_role', 'actor_id', 'idempotency_key', 'command_type', 'target_exam_id', 'request_hash', 'created_at', 'expires_at'):
            self.assertIn(f'BINARY NEW.`{key}`<=>BINARY OLD.`{key}`', update)
        for condition in ('NEW.target_student_id IS NULL', 'NEW.target_attempt_id IS NULL', 'NEW.receipt IS NULL', 'NEW.tombstone=1'):
            self.assertIn(condition, update)
        self.assertIn('IF command_captured THEN SIGNAL', update)
        self.assertIn('IF command_captured THEN SIGNAL', definitions['DELETE'])
        routine = next(s for s in fence._migration_statements() if s.startswith('CREATE PROCEDURE cpm_assert_student_command_erasure('))
        self.assertLess(routine.index('CALL cpm_assert_student_write(NULL)'), routine.index('FROM student_deletion_jobs'))
        self.assertIn('b.job_id=j.id AND b.student_id=j.student_id', routine)
        self.assertIn("'$.command_ids'", routine)
        self.assertIn("('queued','running','waiting','failed')", routine)
        self.assertIn('LOCK IN SHARE MODE', routine)
        self.assertNotIn('@', update + routine)

    def test_privilege_baseline_student_is_guarded_without_generic_id_guessing(self):
        self.assertEqual(fence.student_ids_in({'privilegeBaselineStudentId': '7', 'id': 9, 'actor_id': 10}), {7})
        with self.assertRaises(fence.StudentWriteBlocked):
            fence.student_ids_in({'privilegeBaselineStudentId': True})

    def test_json_journal_schema_drift_fails_closed(self):
        columns, refs, engines = self.schema()
        columns['rating_recalc_jobs'] = {'errors': 'text'}
        engines['rating_recalc_jobs'] = 'InnoDB'
        with self.assertRaisesRegex(fence.StudentFenceUnavailable, 'journal_type_mismatch'):
            fence._validate_ownership_schema(columns, refs, engines)
        columns['rating_recalc_jobs']['errors'] = 'json'
        self.assertIn('rating_recalc_jobs', fence._validate_ownership_schema(columns, refs, engines))

    def test_file_guards_check_old_new_pointers_and_durable_exact_keys(self):
        statements = fence._migration_statements()
        routines = '\n'.join(statements)
        for table, fields in {
            'homework_submissions': ('draft_file_id', 'current_file_id'),
            'homework_file_jobs': ('student_id', 'submission_id', 'homework_id', 'result_file_id', 'staging_key', 'processed_output_key'),
            'homework_submission_files': ('submission_id', 'object_key'),
            'homework_object_ownership': ('object_key', 'student_id', 'submission_id', 'upload_job_id'),
        }.items():
            update = next(body for _, _, event, _, body in fence._trigger_definitions({table}) if event == 'UPDATE')
            for alias in ('OLD', 'NEW'):
                for field in fields:
                    self.assertIn(alias + '.' + field, update)
        object_guard = next(s for s in statements if s.startswith('CREATE PROCEDURE cpm_assert_student_object_write('))
        for table in ('homework_submission_files', 'homework_file_jobs', 'homework_object_ownership', 'homework_student_purge_objects', 'homework_student_purges'):
            self.assertIn(table, object_guard)
        self.assertIn('BINARY o.object_key=BINARY p_key', object_guard)
        self.assertIn('BINARY p.job_id=BINARY o.job_id', object_guard)
        self.assertEqual(object_guard.count('LIMIT 1 LOCK IN SHARE MODE'), 4)
        self.assertIn('cpm_assert_student_write(referenced_owner)', object_guard)
        self.assertNotIn('@', object_guard)

    def test_classic_and_attendance_dual_owner_links_are_guarded(self):
        fields = {
            'classic_exam_attempts': ('assignment_id', 'current_presented_question_id'),
            'classic_exam_presented_questions': ('attempt_id', 'replaces_presented_question_id'),
            'classic_exam_vote_rounds': ('attempt_id', 'presented_question_id'),
            'class_day_attendance': ('student_id', 'zap_id'),
        }
        for table, required in fields.items():
            update = next(body for _, _, event, _, body in fence._trigger_definitions({table}) if event == 'UPDATE')
            for alias in ('OLD', 'NEW'):
                for field in required:
                    self.assertIn(alias + '.' + field, update)
        routines = '\n'.join(fence._migration_statements())
        self.assertIn('assigned_owner<>p_owner', routines)
        self.assertIn('linked_attempt<>p_attempt', routines)
        self.assertIn('linked_owner<>p_owner', routines)

    def test_journal_ids_normalize_padding_without_guessing_generic_ids(self):
        routine = next(s for s in fence._migration_statements() if s.startswith('CREATE PROCEDURE cpm_assert_student_journal_id('))
        self.assertIn("TRIM(LEADING '0' FROM scalar_text)", routine)
        self.assertIn("'^[0-9]+$'", routine)
        self.assertIn('cpm_assert_student_json_id(normalized,FALSE,owner_id)', routine)
        guard = next(s for s in fence._migration_statements() if s.startswith('CREATE PROCEDURE cpm_assert_student_json_write('))
        self.assertNotIn("'$**.id'", guard)
        for key in fence._ID_KEYS | fence._IDS_KEYS | {'student.id'}:
            self.assertIn('$**.' + key, guard)


if __name__ == '__main__':
    unittest.main()
