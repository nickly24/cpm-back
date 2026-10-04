"""Synthetic retired graph tests. No app factory, production config or network.

This SQLite driver exercises ownership selection and saved key snapshots, not MySQL
locking semantics. The live suite exercises the coordinator on MySQL and MongoDB.
"""
import json
import unittest
import sqlite3
from types import SimpleNamespace

from tests import test_student_deletion as core
Fixture, Cursor = core.Fixture, core.Cursor
JOB_ID = '2c43a5d6-f810-43b1-a35e-18b889691dd8'
from cpm_back.services.student_deletion import inventory, jobs, legacy, fk_scope
from cpm_back.services.student_deletion.common import DeletionError


def seed_legacy(fixture):
    db = fixture.db
    # Transitional compatibility while the shared coordinator fixture is upgraded.
    for table, columns in {
        'homework_submissions': {'current_file_id': 'INTEGER', 'draft_file_id': 'INTEGER'},
        'homework_file_jobs': {'homework_id': 'INTEGER', 'result_file_id': 'INTEGER'},
        'homework_object_ownership': {'submission_id': 'INTEGER', 'upload_job_id': 'TEXT'},
    }.items():
        current = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
        for name, type_ in columns.items():
            if name not in current:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {type_}')
    db.execute('UPDATE homework_file_jobs SET homework_id=10 WHERE homework_id IS NULL')
    db.execute("UPDATE homework_file_jobs SET id=? WHERE id='upload1'", (JOB_ID,))
    db.executescript('''
    CREATE TABLE homework_chat_threads(id INTEGER PRIMARY KEY,homework_id INTEGER,student_id INTEGER);
    CREATE TABLE homework_chat_messages(id INTEGER PRIMARY KEY,thread_id INTEGER,sender_role TEXT,sender_id INTEGER,body TEXT);
    CREATE TABLE homework_chat_reads(thread_id INTEGER,reader_role TEXT,reader_id INTEGER,last_message_id INTEGER,PRIMARY KEY(thread_id,reader_role,reader_id));
    CREATE TABLE homework_chat_admin_followers(thread_id INTEGER,admin_id INTEGER,PRIMARY KEY(thread_id,admin_id));
    CREATE TABLE homework_realtime_presence(thread_id INTEGER,actor_role TEXT,actor_id INTEGER,PRIMARY KEY(thread_id,actor_role,actor_id));
    CREATE TABLE homework_realtime_outbox(id INTEGER PRIMARY KEY,thread_id INTEGER,event_type TEXT,entity_id INTEGER,payload_json TEXT);
    CREATE TABLE notifications(id INTEGER PRIMARY KEY,recipient_role TEXT,recipient_id INTEGER,student_id INTEGER,thread_id INTEGER);
    CREATE TABLE push_subscriptions(id INTEGER PRIMARY KEY,user_role TEXT,user_id INTEGER,endpoint TEXT,auth_secret TEXT);
    INSERT INTO homework_chat_threads VALUES(10,10,1),(20,10,2);
    INSERT INTO homework_chat_messages VALUES(101,10,'student',1,'TARGET PRIVATE BODY'),(102,10,'admin',1,'staff reply'),(103,10,'system',NULL,NULL),(201,20,'student',2,'other student'),(202,20,'admin',1,'staff id collision');
    INSERT INTO homework_chat_reads VALUES(10,'student',1,101),(10,'admin',1,102),(20,'student',2,201),(20,'admin',1,202);
    INSERT INTO homework_chat_admin_followers VALUES(10,1),(20,1);
    INSERT INTO homework_realtime_presence VALUES(10,'student',1),(10,'admin',1),(20,'student',2),(20,'admin',1);
    INSERT INTO notifications VALUES(301,'admin',1,1,10),(302,'student',2,2,20),(303,'student',1,NULL,NULL),(304,'admin',1,NULL,NULL),(305,'proctor',1,NULL,10),(306,'admin',1,2,20);
    INSERT INTO push_subscriptions VALUES(401,'student',1,'target endpoint','TARGET SECRET'),(402,'admin',1,'staff endpoint','staff secret'),(403,'student',2,'other endpoint','other secret');
    ''')
    def event(id_, event_type, entity=None, payload=None, thread=None):
        db.execute('INSERT INTO homework_realtime_outbox VALUES(?,?,?,?,?)',
                   (id_, thread, event_type, entity, json.dumps(payload) if payload is not None else None))
    event(501, 'message.created', 101, {'message_id': 101}, 10)
    event(502, 'message.created', 102, None, 10)
    event(503, 'message.read', 101, None, 10)
    event(504, 'message.read', 102, {'reader_role': 'admin', 'reader_id': 1, 'last_message_id': 102}, 10)
    event(505, 'message.read', 0, None, 10)
    event(506, 'typing.changed', payload={'actor_role': 'student', 'actor_id': 1, 'active': True, 'expires_in_ms': 4000}, thread=10)
    event(507, 'submission.changed', 1, {'room': 'user:student:1', 'data': {'homework_id': 10, 'student_id': 1, 'submission_id': 1}})
    event(508, 'job.progress', payload={'room': 'user:student:1', 'data': {'job': {'id': JOB_ID, 'homework_id': 10, 'status': 'running', 'stage': 'processing', 'progress': 50, 'error_code': None}}})
    event(509, 'notification.created', 301, {'room': 'user:admin:1', 'data': {'entity_id': 301}})
    event(510, 'notification.created', 303, {'room': 'user:student:1', 'data': {'entity_id': 303}})
    event(601, 'message.created', 201, None, 20)
    event(602, 'notification.created', 304, {'room': 'user:admin:1', 'data': {'entity_id': 304}})
    event(603, 'submission.changed', 2, {'room': 'user:student:2', 'data': {'homework_id': 10, 'student_id': 2, 'submission_id': 2}})
    fixture.db.commit()


class LegacyGraphTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        seed_legacy(self.fixture)
        self.cursor = Cursor(self.fixture)
        self.addCleanup(self.fixture.db.close)
        self.addCleanup(self.cursor.close)

    def collect(self, context=None):
        columns, _, _ = inventory.schema(self.cursor)
        return legacy.collect_legacy(self.cursor, columns, 1, context=context)

    def add_event(self, event_type, entity=None, payload=None, thread=None, id_=999):
        self.fixture.db.execute('INSERT INTO homework_realtime_outbox VALUES(?,?,?,?,?)',
                                (id_, thread, event_type, entity, json.dumps(payload) if payload is not None else None))

    def error(self):
        with self.assertRaises(DeletionError) as caught:
            self.collect()
        error = caught.exception
        self.assertEqual(error.code, 'legacy_ownership_unresolved')
        self.assertNotIn('TARGET PRIVATE BODY', error.message)
        self.assertNotIn('TARGET SECRET', error.message)
        self.assertNotIn('user:student:', error.message)
        return error

    def test_complete_graph_and_composite_keys_preserve_shared_homework_and_staff_ids(self):
        result = self.collect()
        expected = {'homework_chat_threads': 1, 'homework_chat_messages': 3,
                    'homework_chat_reads': 2, 'homework_chat_admin_followers': 1,
                    'homework_realtime_presence': 2, 'homework_realtime_outbox': 10,
                    'notifications': 3, 'push_subscriptions': 1}
        self.assertEqual({table: len(values) for table, values in result['sql'].items()}, expected)
        self.assertEqual(result['context']['keys']['homework_chat_reads'],
                         [{'thread_id': 10, 'reader_role': 'admin', 'reader_id': 1},
                          {'thread_id': 10, 'reader_role': 'student', 'reader_id': 1}])
        self.assertEqual(result['context']['thread_ids'], [10])
        self.assertEqual(result['context']['submission_ids'], [1])
        self.assertEqual(result['context']['job_ids'], [JOB_ID])
        self.assertNotIn('TARGET PRIVATE BODY', json.dumps(result['context']))
        self.assertNotIn('TARGET SECRET', json.dumps(result['context']))

    def test_legacy_dependencies_order_children_before_parents_without_fks(self):
        order = inventory.deletion_order(legacy.LEGACY_TABLES | {'students'}, [])
        for child, parent in legacy.LEGACY_DEPENDENCIES:
            self.assertLess(order.index(child), order.index(parent))
        self.assertEqual(order[-1], 'students')

    def test_unknown_event_never_interprets_entity_as_student_or_message(self):
        self.add_event('custom.event', entity=1)
        self.assertIn('unsupported_or_conflicting_event=1', self.error().message)

    def test_aggregate_diagnostics_for_unknown_and_orphan_events(self):
        self.add_event('custom.event', entity=101, id_=998)
        self.add_event('message.created', entity=987654, id_=999)
        message = self.error().message
        self.assertIn('unsupported_or_conflicting_event=1', message)
        self.assertIn('orphan_reference=1', message)
        self.assertNotIn('987654', message)

    def test_null_thread_outbox_resolves_only_typed_message_reference(self):
        self.add_event('message.created', entity=101)
        self.assertIn(999, [r['id'] for r in self.collect()['sql']['homework_realtime_outbox']])

    def test_sender_conflicting_with_thread_blocks(self):
        self.fixture.db.execute("INSERT INTO homework_chat_messages VALUES(999,20,'student',1,'hidden')")
        self.assertIn('homework_chat_messages.contradictory_ownership=1', self.error().message)

    def test_reader_conflicting_with_thread_blocks(self):
        self.fixture.db.execute("INSERT INTO homework_chat_reads VALUES(20,'student',1,201)")
        self.assertIn('homework_chat_reads.contradictory_ownership=1', self.error().message)

    def test_last_message_in_another_thread_blocks_even_same_numeric_actor(self):
        self.fixture.db.execute("UPDATE homework_chat_reads SET last_message_id=201 WHERE thread_id=10 AND reader_role='admin'")
        self.assertIn('last_message_thread_mismatch=1', self.error().message)

    def test_dangling_last_message_blocks(self):
        self.fixture.db.execute("UPDATE homework_chat_reads SET last_message_id=999999 WHERE thread_id=10 AND reader_role='admin'")
        self.assertIn('orphan_reference=1', self.error().message)

    def test_presence_conflicting_with_thread_blocks(self):
        self.fixture.db.execute("INSERT INTO homework_realtime_presence VALUES(20,'student',1)")
        self.assertIn('homework_realtime_presence.contradictory_ownership=1', self.error().message)

    def test_notification_scope_is_student_or_student_recipient_or_thread(self):
        self.assertEqual([r['id'] for r in self.collect()['sql']['notifications']], [301, 303, 305])
        self.fixture.db.execute("INSERT INTO notifications VALUES(999,'student',1,2,20)")
        self.assertIn('notifications.contradictory_ownership=1', self.error().message)

    def test_generic_numeric_collision_is_not_student_owner(self):
        self.fixture.db.execute("UPDATE notifications SET id=1 WHERE id=304")
        self.fixture.db.execute("UPDATE homework_realtime_outbox SET entity_id=1,payload_json=? WHERE id=602",
                                (json.dumps({'room': 'user:admin:1', 'data': {'entity_id': 1}}),))
        result = self.collect()
        self.assertNotIn(1, [r['id'] for r in result['sql']['notifications']])
        self.assertNotIn(602, [r['id'] for r in result['sql']['homework_realtime_outbox']])

    def test_outbox_contradictory_message_thread_blocks(self):
        self.add_event('message.created', entity=201, thread=10)
        self.error()

    def test_submission_room_owner_mismatch_blocks(self):
        self.add_event('submission.changed', entity=2, payload={'room': 'user:student:1', 'data': {'homework_id': 10, 'student_id': 1, 'submission_id': 2}})
        self.assertIn('contradictory_ownership=1', self.error().message)

    def test_submission_homework_mismatch_blocks(self):
        self.add_event('submission.changed', entity=1, payload={'room': 'user:student:1', 'data': {'homework_id': 11, 'student_id': 1, 'submission_id': 1}})
        self.error()

    def test_null_submission_variant_is_explicit_student_owned(self):
        self.add_event('submission.changed', payload={'room': 'user:student:1', 'data': {'homework_id': 10, 'student_id': 1, 'submission_id': None}})
        self.assertIn(999, [r['id'] for r in self.collect()['sql']['homework_realtime_outbox']])

    def test_job_id_is_json_string_not_generic_entity(self):
        payload = {'room': 'user:student:1', 'data': {'job': {'id': 'e3fe8a77-f931-42de-85cc-4b0f12fe0123', 'homework_id': 10, 'status': 'running', 'stage': 'x', 'progress': 0, 'error_code': None}}}
        self.add_event('job.progress', payload=payload)
        self.assertIn('orphan_reference=1', self.error().message)

    def test_nested_or_invalid_job_fields_fail_closed(self):
        base = {'id': JOB_ID, 'homework_id': 10, 'status': 'running', 'stage': 'processing', 'progress': 50, 'error_code': None}
        for field, value in [('id', 'arbitrary-job-id'), ('status', {'student_id': 2}), ('stage', []), ('progress', True), ('progress', 101), ('error_code', {'private': 'secret'})]:
            with self.subTest(field=field, value=value):
                payload = {'room': 'user:student:1', 'data': {'job': {**base, field: value}}}
                self.add_event('job.progress', payload=payload)
                self.error()
                self.fixture.db.execute('DELETE FROM homework_realtime_outbox WHERE id=999')

    def test_notification_room_must_match_recipient(self):
        self.add_event('notification.created', entity=301, payload={'room': 'user:student:1', 'data': {'entity_id': 301}})
        self.error()

    def test_unknown_json_keys_fail_closed(self):
        self.add_event('message.created', entity=101, payload={'message_id': 101, 'unreviewed_personal_data': 'hidden'}, thread=10)
        self.error()

    def test_explicit_null_message_reference_is_not_an_absent_payload(self):
        for event,payload in [('message.created', {'message_id': None}),
                              ('message.read', {'reader_role': 'student', 'reader_id': 1, 'last_message_id': None})]:
            with self.subTest(event=event):
                self.add_event(event, entity=101, payload=payload, thread=10)
                self.error()
                self.fixture.db.execute('DELETE FROM homework_realtime_outbox WHERE id=999')

    def test_malformed_payload_fails_without_leaking_content(self):
        self.add_event('message.created', entity=101, thread=10)
        self.fixture.db.execute('UPDATE homework_realtime_outbox SET payload_json=? WHERE id=999', ('{TARGET PRIVATE BODY',))
        self.error()

    def test_orphan_direct_student_sender_is_scoped_without_staff_collision(self):
        self.fixture.db.execute("INSERT INTO homework_chat_messages VALUES(999,99,'student',1,'known sender')")
        result = self.collect()
        self.assertIn(999, [r['id'] for r in result['sql']['homework_chat_messages']])

    def test_orphan_staff_only_presence_is_unresolved_not_silently_skipped(self):
        self.fixture.db.execute("INSERT INTO homework_realtime_presence VALUES(999,'admin',1)")
        self.assertIn('unresolved_ownership=1', self.error().message)

    def test_durable_context_finds_outbox_after_submission_job_thread_and_notification_unlink(self):
        context = self.collect()['context']
        self.fixture.db.execute('DELETE FROM homework_submissions WHERE student_id=1')
        self.fixture.db.execute('DELETE FROM homework_file_jobs WHERE student_id=1')
        self.fixture.db.execute('DELETE FROM homework_chat_threads WHERE student_id=1')
        self.fixture.db.execute('DELETE FROM homework_chat_messages WHERE thread_id=10')
        self.fixture.db.execute('DELETE FROM notifications WHERE id IN (301,303,305)')
        with self.assertRaises(DeletionError) as caught:
            legacy.verify_legacy_residual(self.cursor, 1, context)
        self.assertEqual(caught.exception.code, 'legacy_residual_records')
        self.assertIn('homework_realtime_outbox=10', caught.exception.message)

    def test_exact_saved_keys_detect_reassigned_subscription(self):
        context = self.collect()['context']
        self.delete_selected(context, except_table='push_subscriptions')
        self.fixture.db.execute("UPDATE push_subscriptions SET user_role='admin' WHERE id=401")
        with self.assertRaises(DeletionError) as caught:
            legacy.verify_legacy_residual(self.cursor, 1, context)
        self.assertEqual(caught.exception.code, 'legacy_residual_records')
        self.assertIn('push_subscriptions=1', caught.exception.message)

    def delete_selected(self, context, except_table=None):
        for table, keys in context['keys'].items():
            if table == except_table:
                continue
            for key in keys:
                where = ' AND '.join(field + '=?' for field in key)
                self.fixture.db.execute(f'DELETE FROM {table} WHERE {where}', tuple(key.values()))

    def test_clean_residual_verification_preserves_other_student_and_staff(self):
        context = self.collect()['context']
        self.delete_selected(context)
        self.fixture.db.execute('DELETE FROM homework_submissions WHERE student_id=1')
        self.fixture.db.execute('DELETE FROM homework_file_jobs WHERE student_id=1')
        legacy.verify_legacy_residual(self.cursor, 1, context)
        self.assertEqual(self.fixture.db.execute('SELECT id FROM homework_chat_threads').fetchall(), [(20,)])
        self.assertEqual(self.fixture.db.execute('SELECT id FROM push_subscriptions ORDER BY id').fetchall(), [(402,), (403,)])
        self.assertEqual(self.fixture.db.execute('SELECT id FROM homework_realtime_outbox ORDER BY id').fetchall(), [(601,), (602,), (603,)])
        self.assertEqual(self.fixture.db.execute('SELECT id FROM homework').fetchall(), [(10,)])

    def test_missing_legacy_column_and_wrong_pk_block_schema(self):
        columns, pks, _ = inventory.schema(self.cursor)
        columns['homework_chat_reads'] = [c for c in columns['homework_chat_reads'] if c['COLUMN_NAME'] != 'reader_role']
        with self.assertRaises(DeletionError):
            legacy.validate_legacy_schema(columns, pks)


class LegacyCoordinatorTests(unittest.TestCase):
    def setUp(self):
        core.CoordinatorTests.setUp(self)
        seed_legacy(self.fixture)

    start = core.CoordinatorTests.start
    finish = core.CoordinatorTests.finish

    def test_preview_and_whole_pipeline_include_retired_graph(self):
        view=jobs.preview(1,self.actor)
        self.assertTrue(view['available'],view['blockers'])
        self.assertTrue(any(d['key']=='homework_chat_messages' for d in view['details']))
        result=self.finish(self.start())
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(self.fixture.db.execute('SELECT id FROM homework_chat_threads').fetchall(),[(20,)])
        self.assertEqual(self.fixture.db.execute("SELECT endpoint FROM push_subscriptions WHERE user_role='proctor'").fetchall(),[])
        self.assertEqual(self.fixture.db.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_role='admin'").fetchone()[0],1)

    def test_unknown_legacy_event_is_reported_for_manual_cleanup(self):
        self.fixture.db.execute("INSERT INTO homework_realtime_outbox VALUES(999,NULL,'unknown',1,NULL)")
        self.fixture.db.commit()
        view=jobs.preview(1,self.actor)
        self.assertTrue(view['available'],view['blockers'])
        self.assertTrue(view['warnings'])
        result=self.finish(self.start())
        self.assertEqual(result['status'],'partial')
        self.assertTrue(result['warnings'])
        self.assertEqual(self.fixture.db.execute('SELECT id FROM homework_chat_threads').fetchall(),[(20,)])
        self.assertEqual(self.fixture.db.execute('SELECT id FROM homework_realtime_outbox WHERE id=999').fetchall(),[(999,)])


AUDITED_FK_ROWS = (('admin_role_permissions', 'fk_admin_permission_role', 'role_id', 'admin_roles', 'id', 1),
 ('admin_role_users', 'fk_admin_role_user_role', 'role_id', 'admin_roles', 'id', 1),
 ('attendance', 'attendance_ibfk_1', 'student_id', 'students', 'id', 1),
 ('cards', 'cards_ibfk_1', 'theme_id', 'card_themes', 'id', 1),
 ('class_day_attendance', 'fk_cda_class_day', 'class_day_id', 'class_days', 'id', 1),
 ('class_day_attendance', 'fk_cda_student', 'student_id', 'students', 'id', 1),
 ('class_day_attendance', 'fk_cda_type', 'attendance_type_id', 'attendance_types', 'id', 1),
 ('class_day_attendance', 'fk_cda_zap', 'zap_id', 'zaps', 'id', 1),
 ('classic_exam_appeals', 'fk_classic_appeal_attempt', 'attempt_id', 'classic_exam_attempts', 'id', 1),
 ('classic_exam_assignment_import_sessions', 'fk_assignment_import_exam', 'exam_id', 'exams', 'id', 1),
 ('classic_exam_assignment_members',
  'fk_classic_am_assignment',
  'assignment_id',
  'classic_exam_assignments',
  'id',
  1),
 ('classic_exam_assignment_members', 'fk_classic_am_examinator', 'examinator_id', 'examinators', 'id', 1),
 ('classic_exam_assignments',
  'fk_classic_assignment_commission',
  'source_commission_id',
  'classic_exam_commissions',
  'id',
  1),
 ('classic_exam_assignments', 'fk_classic_assignment_exam', 'exam_id', 'exams', 'id', 1),
 ('classic_exam_assignments', 'fk_classic_assignment_student', 'student_id', 'students', 'id', 1),
 ('classic_exam_attempt_commands',
  'fk_classic_command_attempt',
  'attempt_id',
  'classic_exam_attempts',
  'id',
  1),
 ('classic_exam_attempt_commands',
  'fk_classic_command_examinator',
  'actor_examinator_id',
  'examinators',
  'id',
  1),
 ('classic_exam_attempt_members', 'fk_classic_atm_attempt', 'attempt_id', 'classic_exam_attempts', 'id', 1),
 ('classic_exam_attempt_members', 'fk_classic_atm_examinator', 'examinator_id', 'examinators', 'id', 1),
 ('classic_exam_attempt_part_progress',
  'fk_classic_progress_attempt',
  'attempt_id',
  'classic_exam_attempts',
  'id',
  1),
 ('classic_exam_attempt_question_usage',
  'fk_classic_usage_attempt',
  'attempt_id',
  'classic_exam_attempts',
  'id',
  1),
 ('classic_exam_attempt_question_usage',
  'fk_classic_usage_definition',
  'definition_question_id',
  'classic_exam_definition_questions',
  'id',
  1),
 ('classic_exam_attempts',
  'fk_classic_attempt_assignment',
  'assignment_id',
  'classic_exam_assignments',
  'id',
  1),
 ('classic_exam_attempts',
  'fk_classic_attempt_definition',
  'definition_id',
  'classic_exam_definition_versions',
  'id',
  1),
 ('classic_exam_attempts', 'fk_classic_attempt_exam', 'exam_id', 'exams', 'id', 1),
 ('classic_exam_attempts', 'fk_classic_attempt_student', 'student_id', 'students', 'id', 1),
 ('classic_exam_commission_members',
  'fk_classic_cm_commission',
  'commission_id',
  'classic_exam_commissions',
  'id',
  1),
 ('classic_exam_commission_members', 'fk_classic_cm_examinator', 'examinator_id', 'examinators', 'id', 1),
 ('classic_exam_commissions', 'fk_classic_commission_exam', 'exam_id', 'exams', 'id', 1),
 ('classic_exam_definition_questions',
  'fk_classic_dq_definition',
  'definition_id',
  'classic_exam_definition_versions',
  'id',
  1),
 ('classic_exam_definition_versions', 'fk_classic_definition_exam', 'exam_id', 'exams', 'id', 1),
 ('classic_exam_grade_thresholds', 'fk_classic_threshold_exam', 'exam_id', 'exams', 'id', 1),
 ('classic_exam_parts', 'fk_classic_part_exam', 'exam_id', 'exams', 'id', 1),
 ('classic_exam_presented_questions',
  'fk_classic_presented_attempt',
  'attempt_id',
  'classic_exam_attempts',
  'id',
  1),
 ('classic_exam_presented_questions',
  'fk_classic_presented_definition',
  'definition_question_id',
  'classic_exam_definition_questions',
  'id',
  1),
 ('classic_exam_presented_questions',
  'fk_classic_presented_replacer',
  'replaced_by_examinator_id',
  'examinators',
  'id',
  1),
 ('classic_exam_question_import_sessions', 'fk_question_import_exam', 'exam_id', 'exams', 'id', 1),
 ('classic_exam_questions', 'fk_classic_question_exam', 'exam_id', 'exams', 'id', 1),
 ('classic_exam_questions', 'fk_classic_question_part', 'part_id', 'classic_exam_parts', 'id', 1),
 ('classic_exam_settings', 'fk_classic_settings_exam', 'exam_id', 'exams', 'id', 1),
 ('classic_exam_settings', 'fk_classic_settings_part', 'tie_breaker_part_id', 'classic_exam_parts', 'id', 1),
 ('classic_exam_student_privileges', 'fk_classic_privilege_exam', 'exam_id', 'exams', 'id', 1),
 ('classic_exam_student_privileges', 'fk_classic_privilege_student', 'student_id', 'students', 'id', 1),
 ('classic_exam_vote_rounds', 'fk_classic_round_attempt', 'attempt_id', 'classic_exam_attempts', 'id', 1),
 ('classic_exam_vote_rounds',
  'fk_classic_round_presented',
  'presented_question_id',
  'classic_exam_presented_questions',
  'id',
  1),
 ('classic_exam_votes', 'fk_classic_vote_examinator', 'examinator_id', 'examinators', 'id', 1),
 ('classic_exam_votes', 'fk_classic_vote_round', 'round_id', 'classic_exam_vote_rounds', 'id', 1),
 ('exam_sessions', 'fk_exam_sessions_exam', 'exam_id', 'exams', 'id', 1),
 ('exam_sessions', 'fk_exam_sessions_student', 'student_id', 'students', 'id', 1),
 ('exams', 'fk_exams_direction', 'direction_id', 'directions', 'id', 1),
 ('homework_chat_admin_followers', 'fk_hw_followers_thread', 'thread_id', 'homework_chat_threads', 'id', 1),
 ('homework_chat_messages', 'fk_hw_messages_thread', 'thread_id', 'homework_chat_threads', 'id', 1),
 ('homework_chat_reads', 'fk_hw_reads_thread', 'thread_id', 'homework_chat_threads', 'id', 1),
 ('homework_file_jobs', 'fk_hw_jobs_submission', 'submission_id', 'homework_submissions', 'id', 1),
 ('homework_sessions', 'homework_sessions_ibfk_1', 'homework_id', 'homework', 'id', 1),
 ('homework_sessions', 'homework_sessions_ibfk_2', 'student_id', 'students', 'id', 1),
 ('homework_submission_files', 'fk_hw_files_submission', 'submission_id', 'homework_submissions', 'id', 1),
 ('outside_exam_result_import_sessions', 'fk_outside_import_exam', 'exam_id', 'exams', 'id', 1),
 ('rating_recalc_staging', 'fk_rating_staging_job', 'job_id', 'rating_recalc_jobs', 'id', 1),
 ('student_credentials', 'fk_student_credentials_student', 'student_id', 'students', 'id', 1),
 ('student_progress', 'student_progress_ibfk_1', 'theme_id', 'card_themes', 'id', 1),
 ('student_progress', 'student_progress_ibfk_2', 'question_id', 'cards', 'id', 1),
 ('telegram_bot_chats', 'fk_telegram_bot_chats_student', 'student_id', 'students', 'id', 1),
 ('user_import_job_results', 'fk_user_import_job_results_job', 'job_id', 'user_import_jobs', 'id', 1),
 ('zap_dates', 'fk_zd_class_day', 'class_day_id', 'class_days', 'id', 1),
 ('zap_dates', 'fk_zd_zap', 'zap_id', 'zaps', 'id', 1))

AUDITED_STRUCTURAL_COLUMNS = {'Allratings': [('id', True)],
 'admin_role_permissions': [('role_id', True), ('section', True)],
 'admin_role_users': [('id', True), ('role_id', False)],
 'admin_roles': [('id', True)],
 'admins': [('id', True)],
 'application_settings': [('setting_key', True)],
 'attendance': [('id', True), ('student_id', False)],
 'attendance_types': [('id', True)],
 'auth_users': [('id', True)],
 'card_themes': [('id', True)],
 'cards': [('id', True), ('theme_id', False)],
 'class_day_attendance': [('attendance_type_id', False),
                          ('class_day_id', False),
                          ('id', True),
                          ('student_id', False),
                          ('zap_id', False)],
 'class_days': [('id', True)],
 'classic_exam_appeals': [('attempt_id', False), ('id', True)],
 'classic_exam_assignment_import_sessions': [('exam_id', False), ('id', True)],
 'classic_exam_assignment_members': [('assignment_id', True), ('examinator_id', True)],
 'classic_exam_assignments': [('exam_id', False),
                              ('id', True),
                              ('source_commission_id', False),
                              ('student_id', False)],
 'classic_exam_attempt_commands': [('actor_examinator_id', False), ('attempt_id', False), ('id', True)],
 'classic_exam_attempt_members': [('attempt_id', True), ('examinator_id', True)],
 'classic_exam_attempt_part_progress': [('attempt_id', True), ('source_part_id', True)],
 'classic_exam_attempt_question_usage': [('attempt_id', True), ('definition_question_id', True)],
 'classic_exam_attempts': [('assignment_id', False),
                           ('current_presented_question_id', False),
                           ('definition_id', False),
                           ('exam_id', False),
                           ('id', True),
                           ('student_id', False)],
 'classic_exam_commission_members': [('commission_id', True), ('examinator_id', True)],
 'classic_exam_commissions': [('exam_id', False), ('id', True)],
 'classic_exam_definition_questions': [('definition_id', False), ('id', True)],
 'classic_exam_definition_versions': [('exam_id', False), ('id', True)],
 'classic_exam_grade_thresholds': [('exam_id', True), ('grade', True)],
 'classic_exam_parts': [('exam_id', False), ('id', True)],
 'classic_exam_presented_questions': [('attempt_id', False),
                                      ('definition_question_id', False),
                                      ('id', True),
                                      ('replaced_by_examinator_id', False),
                                      ('replaces_presented_question_id', False)],
 'classic_exam_question_import_sessions': [('exam_id', False), ('id', True)],
 'classic_exam_questions': [('exam_id', False), ('id', True), ('part_id', False)],
 'classic_exam_settings': [('exam_id', True), ('tie_breaker_part_id', False)],
 'classic_exam_student_privileges': [('exam_id', True), ('student_id', True)],
 'classic_exam_vote_rounds': [('attempt_id', False), ('id', True), ('presented_question_id', False)],
 'classic_exam_votes': [('examinator_id', False), ('id', True), ('round_id', False)],
 'directions': [('id', True)],
 'exam_admin_commands': [('id', True)],
 'exam_schema_migration_steps': [('migration', True), ('step_id', True)],
 'exam_sessions': [('exam_id', False), ('id', True), ('student_id', False)],
 'examinators': [('id', True)],
 'exams': [('direction_id', False), ('id', True)],
 'groups': [('id', True)],
 'homework': [('id', True)],
 'homework_chat_admin_followers': [('admin_id', True), ('thread_id', True)],
 'homework_chat_messages': [('id', True), ('thread_id', False)],
 'homework_chat_reads': [('reader_id', True), ('reader_role', True), ('thread_id', True)],
 'homework_chat_threads': [('id', True)],
 'homework_file_jobs': [('id', True), ('submission_id', False)],
 'homework_realtime_outbox': [('id', True)],
 'homework_realtime_presence': [('actor_id', True), ('actor_role', True), ('thread_id', True)],
 'homework_s3_delete_queue': [('id', True)],
 'homework_sessions': [('homework_id', False), ('id', True), ('student_id', False)],
 'homework_submission_files': [('id', True), ('submission_id', False)],
 'homework_submissions': [('id', True)],
 'notifications': [('id', True)],
 'outside_exam_result_import_sessions': [('exam_id', False), ('id', True)],
 'proctors': [('id', True)],
 'push_subscriptions': [('id', True)],
 'rating_recalc_jobs': [('id', True)],
 'rating_recalc_staging': [('job_id', True), ('student_id', True)],
 'rating_source_state': [('id', True)],
 'schema_migrations': [('version', True)],
 'schools': [('id', True)],
 'student_card_progress': [('id', True)],
 'student_credentials': [('student_id', True)],
 'student_progress': [('id', True), ('question_id', False), ('theme_id', False)],
 'student_section_study_settings': [('id', True)],
 'students': [('id', True)],
 'supervisors': [('id', True)],
 'telegram_bot_chats': [('chat_id', True), ('student_id', False)],
 'telegram_bot_settings': [('id', True)],
 'test_sessions': [('id', True)],
 'tests_out': [('id', True)],
 'user_import_job_results': [('job_id', True)],
 'user_import_jobs': [('id', True)],
 'user_import_sessions': [('id', True)],
 'zap_dates': [('class_day_id', False), ('id', True), ('zap_id', False)],
 'zap_img': [('id', True)],
 'zaps': [('id', True)]}

class ForeignKeyScopeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.fixture.db.executescript("""
        ALTER TABLE classic_exam_attempts ADD COLUMN current_presented_question_id INTEGER;
        ALTER TABLE classic_exam_presented_questions ADD COLUMN replaces_presented_question_id INTEGER;
        CREATE TABLE classic_exam_vote_rounds(id INTEGER PRIMARY KEY,attempt_id INTEGER,presented_question_id INTEGER);
        CREATE TABLE classic_exam_votes(id INTEGER PRIMARY KEY,round_id INTEGER);
        CREATE TABLE zaps(id INTEGER PRIMARY KEY,student_id INTEGER);
        CREATE TABLE class_day_attendance(id INTEGER PRIMARY KEY,student_id INTEGER,zap_id INTEGER);
        INSERT INTO classic_exam_vote_rounds VALUES(1,1,1),(2,2,2);
        INSERT INTO classic_exam_votes VALUES(1,1),(2,2);
        INSERT INTO zaps VALUES(1,1),(2,2);
        INSERT INTO class_day_attendance VALUES(1,1,1),(2,2,2);
        """)
        pairs = [('classic_exam_attempts','assignment_id','classic_exam_assignments'),
                 ('classic_exam_attempts','student_id','students'),
                 ('classic_exam_presented_questions','attempt_id','classic_exam_attempts'),
                 ('classic_exam_vote_rounds','attempt_id','classic_exam_attempts'),
                 ('classic_exam_vote_rounds','presented_question_id','classic_exam_presented_questions'),
                 ('classic_exam_votes','round_id','classic_exam_vote_rounds'),
                 ('class_day_attendance','student_id','students'),
                 ('class_day_attendance','zap_id','zaps')]
        self.fixture.foreign_keys = [dict(TABLE_NAME=t,CONSTRAINT_NAME=t+'_'+c,COLUMN_NAME=c,
                                         REFERENCED_TABLE_NAME=p,REFERENCED_COLUMN_NAME='id',ORDINAL_POSITION=1)
                                     for t,c,p in pairs]
        self.cursor = Cursor(self.fixture)
        self.addCleanup(self.fixture.db.close)
        self.addCleanup(self.cursor.close)

    def snapshot(self):
        columns, pks, fks = inventory.schema(self.cursor)
        selected = {}
        for table,(where,args) in inventory.selectors(columns,1).items():
            self.cursor.execute('SELECT * FROM `'+table+'` WHERE '+where,args)
            selected[table] = self.cursor.fetchall()
        return columns,pks,fks,selected

    def check(self):
        return fk_scope.check_fk_scope(self.cursor,*self.snapshot())

    def error(self, expected):
        before = list(self.fixture.db.iterdump())
        with self.assertRaises(DeletionError) as caught:
            self.check()
        self.assertEqual(caught.exception.code,'student_fk_scope_conflict')
        self.assertIn(expected,caught.exception.message)
        self.assertNotIn('Synthetic Target',caught.exception.message)
        self.assertEqual(before,list(self.fixture.db.iterdump()))

    def test_valid_graph_has_reference_only_context(self):
        context = self.check()
        self.assertEqual(context['version'],1)
        self.assertNotIn('Synthetic Target',json.dumps(context))
        self.assertNotIn('synthetic',json.dumps(context))

    def test_other_round_pointing_to_selected_question_cannot_cascade(self):
        self.fixture.db.execute('UPDATE classic_exam_vote_rounds SET presented_question_id=1 WHERE id=2')
        self.error('classic_exam_vote_rounds.incoming_outside_authorized_scope=1')
        self.assertEqual(self.fixture.db.execute('SELECT id FROM classic_exam_votes').fetchall(),[(1,),(2,)])

    def test_selected_round_cannot_reach_other_question(self):
        self.fixture.db.execute('UPDATE classic_exam_vote_rounds SET presented_question_id=2 WHERE id=1')
        self.error('classic_exam_vote_rounds.parent_outside_authorized_scope=1')

    def test_other_attempt_referencing_selected_assignment_blocks_restrict(self):
        self.fixture.db.execute('UPDATE classic_exam_attempts SET assignment_id=1 WHERE id=2')
        self.error('classic_exam_attempts.incoming_outside_authorized_scope=1')

    def test_other_attendance_referencing_selected_zap_blocks_set_null(self):
        self.fixture.db.execute('UPDATE class_day_attendance SET zap_id=1 WHERE id=2')
        self.error('class_day_attendance.incoming_outside_authorized_scope=1')

    def test_selected_attendance_referencing_other_zap_blocks(self):
        self.fixture.db.execute('UPDATE class_day_attendance SET zap_id=2 WHERE id=1')
        self.error('class_day_attendance.parent_outside_authorized_scope=1')

    def test_current_question_no_fk_cross_attempt_blocks_both_directions(self):
        self.fixture.db.execute('UPDATE classic_exam_attempts SET current_presented_question_id=1 WHERE id=2')
        self.error('semantic_incoming_outside_authorized_scope=1')
        self.fixture.db.execute('UPDATE classic_exam_attempts SET current_presented_question_id=NULL WHERE id=2')
        self.fixture.db.execute('UPDATE classic_exam_attempts SET current_presented_question_id=2 WHERE id=1')
        self.error('current_presented_question_id_attempt_mismatch=1')

    def test_replacement_question_no_fk_cross_attempt_blocks_both_directions(self):
        self.fixture.db.execute('UPDATE classic_exam_presented_questions SET replaces_presented_question_id=1 WHERE id=2')
        self.error('semantic_incoming_outside_authorized_scope=1')
        self.fixture.db.execute('UPDATE classic_exam_presented_questions SET replaces_presented_question_id=NULL WHERE id=2')
        self.fixture.db.execute('UPDATE classic_exam_presented_questions SET replaces_presented_question_id=2 WHERE id=1')
        self.error('replaces_presented_question_id_attempt_mismatch=1')

    def test_same_student_different_attempt_still_rejects_question_mismatch(self):
        self.fixture.db.execute('UPDATE classic_exam_assignments SET student_id=1 WHERE id=2')
        self.fixture.db.execute('UPDATE classic_exam_attempts SET student_id=1,current_presented_question_id=1 WHERE id=2')
        self.error('current_presented_question_id_attempt_mismatch=1')

    def test_unknown_incoming_dependency_is_not_whitelisted(self):
        self.fixture.db.execute('CREATE TABLE unexpected_dependency(id INTEGER PRIMARY KEY,target_id INTEGER)')
        self.fixture.foreign_keys.append(dict(TABLE_NAME='unexpected_dependency',COLUMN_NAME='target_id',
            REFERENCED_TABLE_NAME='classic_exam_presented_questions',REFERENCED_COLUMN_NAME='id'))
        self.fixture.db.execute('INSERT INTO unexpected_dependency VALUES(1,1)')
        with self.assertRaises(DeletionError) as caught:
            self.check()
        self.assertEqual(caught.exception.code,'student_fk_scope_conflict')

    def test_stage_recheck_rejects_late_outside_row_without_extending_manifest(self):
        columns,pks,fks,selected=self.snapshot()
        context=fk_scope.check_fk_scope(self.cursor,columns,pks,fks,selected)
        manifest={'sql':{table:[{key:row[key] for key in pks[table]} for row in values] for table,values in selected.items()},
                  'fk_ref_context':context}
        self.fixture.db.execute('UPDATE classic_exam_vote_rounds SET presented_question_id=1 WHERE id=2')
        with self.assertRaises(DeletionError) as caught:
            fk_scope.verify_manifest_fk_scope(self.cursor,manifest)
        self.assertEqual(caught.exception.code,'student_fk_scope_conflict')

    def test_new_fk_after_manifest_fails_closed_before_destructive_stage(self):
        columns,pks,fks,selected=self.snapshot()
        context=fk_scope.check_fk_scope(self.cursor,columns,pks,fks,selected)
        manifest={'sql':{table:[{key:row[key] for key in pks[table]} for row in values] for table,values in selected.items()},
                  'fk_ref_context':context}
        self.fixture.foreign_keys.append(dict(TABLE_NAME='attendance',CONSTRAINT_NAME='new_fk',COLUMN_NAME='student_id',
            REFERENCED_TABLE_NAME='students',REFERENCED_COLUMN_NAME='id',ORDINAL_POSITION=1))
        with self.assertRaises(DeletionError) as caught:
            fk_scope.verify_manifest_fk_scope(self.cursor,manifest)
        self.assertEqual(caught.exception.code,'student_fk_scope_unavailable')
        self.assertIn('changed_fk_constraints=1',caught.exception.message)

    def test_completed_child_stage_uses_saved_refs_without_reauthorizing_other_rows(self):
        columns,pks,fks,selected=self.snapshot()
        context=fk_scope.check_fk_scope(self.cursor,columns,pks,fks,selected)
        manifest={'sql':{table:[{key:row[key] for key in pks[table]} for row in values] for table,values in selected.items()},
                  'fk_ref_context':context}
        for table in ('classic_exam_votes','classic_exam_vote_rounds','classic_exam_presented_questions'):
            self.fixture.db.execute('DELETE FROM '+table+' WHERE id=1')
        fk_scope.verify_manifest_fk_scope(self.cursor,manifest)
        self.fixture.db.execute('UPDATE classic_exam_vote_rounds SET presented_question_id=1 WHERE id=2')
        with self.assertRaises(DeletionError) as caught:
            fk_scope.verify_manifest_fk_scope(self.cursor,manifest)
        self.assertEqual(caught.exception.code,'student_fk_scope_conflict')

    def test_composite_fk_pairing_does_not_cross_match_columns(self):
        self.fixture.db.executescript('CREATE TABLE composite_parent(owner INTEGER,sequence INTEGER,PRIMARY KEY(owner,sequence));\n            CREATE TABLE composite_child(id INTEGER PRIMARY KEY,owner INTEGER,sequence INTEGER);\n            INSERT INTO composite_parent VALUES(1,7),(2,8);\n            INSERT INTO composite_child VALUES(1,1,7),(2,2,8);')
        columns,pks,_,_=self.snapshot()
        fks=[dict(TABLE_NAME='composite_child',CONSTRAINT_NAME='pair',COLUMN_NAME=c,ORDINAL_POSITION=i,
                  REFERENCED_TABLE_NAME='composite_parent',REFERENCED_COLUMN_NAME=c)
             for i,c in enumerate(('owner','sequence'),1)]
        selected={'composite_parent':[{'owner':1,'sequence':7}],
                  'composite_child':[{'id':1,'owner':1,'sequence':7}]}
        fk_scope.check_fk_scope(self.cursor,columns,pks,fks,selected)
        self.fixture.db.execute('UPDATE composite_child SET owner=1,sequence=7 WHERE id=2')
        with self.assertRaises(DeletionError):
            fk_scope.check_fk_scope(self.cursor,columns,pks,fks,selected)
        self.assertEqual(len(fk_scope.grouped_foreign_keys(fks)),1)

    def test_full_audited_79_table_66_fk_metadata_graph(self):
        db=sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        columns={};pks={}
        for table,fields in AUDITED_STRUCTURAL_COLUMNS.items():
            names=[name for name,_ in fields];primary=[name for name,is_pk in fields if is_pk]
            ddl=','.join('`'+name+'` INTEGER' for name in names)
            if primary:ddl+=',PRIMARY KEY('+','.join('`'+name+'`' for name in primary)+')'
            db.execute('CREATE TABLE `'+table+'`('+ddl+')')
            columns[table]=[{'COLUMN_NAME':name,'COLUMN_KEY':'PRI' if is_pk else ''} for name,is_pk in fields]
            pks[table]=primary
        fks=[dict(TABLE_NAME=t,CONSTRAINT_NAME=n,COLUMN_NAME=c,REFERENCED_TABLE_NAME=p,REFERENCED_COLUMN_NAME=pc,ORDINAL_POSITION=o)
             for t,n,c,p,pc,o in AUDITED_FK_ROWS]
        self.assertEqual(len(columns),79);self.assertEqual(len(fks),66)
        fixture=SimpleNamespace(db=db,foreign_keys=fks);cursor=Cursor(fixture);self.addCleanup(cursor.close)
        # Empty shared/reference tables remain present. Personal rows model two
        # owners; unknown future graph descendants would have to pass the gate.
        inserts={'students':[{'id':1},{'id':2}],
                 'classic_exam_assignments':[{'id':1,'student_id':1},{'id':2,'student_id':2}],
                 'classic_exam_attempts':[{'id':1,'student_id':1,'assignment_id':1},{'id':2,'student_id':2,'assignment_id':2}],
                 'classic_exam_presented_questions':[{'id':1,'attempt_id':1},{'id':2,'attempt_id':2}],
                 'classic_exam_vote_rounds':[{'id':1,'attempt_id':1,'presented_question_id':1},{'id':2,'attempt_id':2,'presented_question_id':2}]}
        for table,values in inserts.items():
            for row in values:
                db.execute('INSERT INTO `'+table+'`('+','.join('`'+key+'`' for key in row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
        scope=(set(inventory.DIRECT)|set(inventory.CHILDREN)|legacy.LEGACY_TABLES|inventory.ACCOUNT)&set(columns)
        selected={table:[] for table in scope}
        for table in inserts:
            cursor.execute('SELECT * FROM `'+table+'` WHERE id=1');selected[table]=cursor.fetchall()
        fk_scope.check_fk_scope(cursor,columns,pks,fks,selected)
        db.execute('UPDATE classic_exam_vote_rounds SET presented_question_id=1 WHERE id=2')
        with self.assertRaises(DeletionError) as caught:
            fk_scope.check_fk_scope(cursor,columns,pks,fks,selected)
        self.assertIn('classic_exam_vote_rounds.incoming_outside_authorized_scope=1',caught.exception.message)


if __name__ == '__main__':
    unittest.main()
