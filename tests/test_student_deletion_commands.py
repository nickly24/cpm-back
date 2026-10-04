"""Actual persisted exam preview shapes and command idempotency contracts."""
from datetime import timedelta
import json
import unittest
from unittest.mock import Mock, patch
import uuid

from tests import test_student_deletion as fixture
from cpm_back.services.student_deletion import jobs, retention, commands
from cpm_back.services.student_deletion.common import DeletionError
from cpm_back.services.exams import imports, common, maintenance


class AuditedExamOwnershipTests(unittest.TestCase):
    setUp = fixture.CoordinatorTests.setUp
    start = fixture.CoordinatorTests.start

    def finish(self, job):
        self.fixture.finish_homework(job)
        for _ in range(4): self.fixture.due(); jobs.run_once()
        self.assertEqual(jobs.get_job(job['id'])['status'], 'completed')

    def stored_preview(self, rows):
        self.fixture.db.execute('CREATE TABLE classic_exam_assignment_import_sessions(id INTEGER PRIMARY KEY,preview_payload TEXT,commit_result TEXT)')
        stored = imports.stored_rows(rows)
        self.assertTrue(all('resolved' not in row for row in stored))
        self.fixture.db.execute('INSERT INTO classic_exam_assignment_import_sessions VALUES(1,?,NULL)', (json.dumps(stored),))
        self.fixture.db.commit()
        return stored

    def command(self, ident, kind, receipt, student=None, attempt=None, tombstone=0):
        self.fixture.db.execute('''INSERT INTO exam_admin_commands
          (id,idempotency_key,command_type,target_exam_id,target_student_id,target_attempt_id,receipt,tombstone)
          VALUES(?,?,?,20,?,?,?,?)''', (ident, str(uuid.uuid4()), kind, student, attempt, json.dumps(receipt) if receipt is not None else None, tombstone))
        self.fixture.db.commit()

    def test_actual_baseline_preview_removed_after_login_renamed(self):
        self.fixture.db.execute("UPDATE auth_users SET username='new_login' WHERE ref_id=1")
        self.fixture.db.execute("UPDATE student_credentials SET login='new_login' WHERE student_id=1")
        rows = [
            {'rowId': 'row-2', 'sourceRow': 2, 'excluded': False,
             'input': {'studentLogin': 'historic_login', 'replacementLimit': 2},
             'privilegeBaselineStudentId': 1, 'privilegeBaselineVersion': 7,
             'resolved': {'student': {'id': 1}}},
            {'rowId': 'row-3', 'sourceRow': 3, 'excluded': False,
             'input': {'studentLogin': 'new_login', 'replacementLimit': 0},
             'privilegeBaselineStudentId': 2, 'privilegeBaselineVersion': 4,
             'resolved': {'student': {'id': 2}}},
        ]
        stored = self.stored_preview(rows)
        self.finish(self.start())
        kept = json.loads(self.fixture.db.execute('SELECT preview_payload FROM classic_exam_assignment_import_sessions').fetchone()[0])
        self.assertEqual(kept, [stored[1]])

    def test_actual_input_id_conflicts_with_privilege_baseline(self):
        self.stored_preview([{'rowId': 'row-2', 'sourceRow': 2, 'excluded': False,
                             'input': {'studentId': 2, 'studentLogin': 'synth2'},
                             'privilegeBaselineStudentId': 1, 'privilegeBaselineVersion': 5}])
        preview = jobs.preview(1, self.actor)
        self.assertFalse(preview['available'])
        self.assertIsNone(preview['confirmation_token'])
        self.assertEqual(self.fixture.db.execute('SELECT COUNT(*) FROM students').fetchone()[0], 3)

    def test_all_explicit_array_forms_preserve_other_ids(self):
        value = {'student_ids': [1, 2], 'studentIds': ['0001', '2'], 'student_ids_created': [1, 3]}
        self.assertEqual(retention.scrub(value, 1, set()), {'student_ids': [2], 'studentIds': ['2'], 'student_ids_created': [3]})

    def test_zero_padded_actual_baseline_is_same_immutable_student(self):
        self.stored_preview([{'rowId': 'row-2', 'sourceRow': 2, 'excluded': False,
                             'input': {'studentLogin': 'former-login'},
                             'privilegeBaselineStudentId': '0001', 'privilegeBaselineVersion': 5}])
        self.finish(self.start())
        self.assertEqual(json.loads(self.fixture.db.execute('SELECT preview_payload FROM classic_exam_assignment_import_sessions').fetchone()[0]), [])

    def test_assignment_only_receipt_owner_resolved_before_parent_erasure(self):
        self.command(1, 'PUT:exams_v2.assignment_detail', {'assignmentId': 1})
        self.command(2, 'PUT:exams_v2.assignment_detail', {'assignmentId': 2})
        self.finish(self.start())
        self.assertEqual(self.fixture.db.execute('SELECT tombstone,receipt FROM exam_admin_commands WHERE id=1').fetchone(), (1, None))
        self.assertEqual(json.loads(self.fixture.db.execute('SELECT receipt FROM exam_admin_commands WHERE id=2').fetchone()[0]), {'assignmentId': 2})

    def test_shared_import_and_exam_receipts_stay_verbatim(self):
        shared = {'sessionId': 5, 'committed': True}
        self.command(1, 'POST:exam_imports.commit', shared)
        self.command(2, 'PATCH:exams_v2.classic_config', {'examId': 20, 'commandId': 2, 'outcome': 'applied'})
        before = self.fixture.db.execute('SELECT * FROM exam_admin_commands ORDER BY id').fetchall()
        self.finish(self.start())
        self.assertEqual(self.fixture.db.execute('SELECT * FROM exam_admin_commands ORDER BY id').fetchall(), before)

    def test_unknown_mixed_shared_receipt_blocks_without_generic_drop(self):
        self.command(1, 'POST:exam_imports.commit', {'sessionId': 5, 'committed': True, 'rows': [{'studentId': 1}, {'studentId': 2}]})
        preview = jobs.preview(1, self.actor)
        self.assertFalse(preview['available'])
        self.assertIn('unsupported_receipt_fields=1', preview['blockers'][0])
        self.assertEqual(self.fixture.db.execute('SELECT tombstone FROM exam_admin_commands').fetchone()[0], 0)

    def test_command_owner_disagreement_blocks(self):
        self.command(1, 'POST:exams_v2.classic_student_retake', {'assignmentId': 2, 'studentId': 1}, student=1)
        self.assertFalse(jobs.preview(1, self.actor)['available'])

    def test_ordinary_deleted_assignment_receipts_do_not_block_unrelated_student(self):
        self.command(1, 'POST:exams_v2.assignments_collection', {'assignmentId': 2, 'studentId': 2}, student=2)
        self.command(2, 'PUT:exams_v2.assignment_detail', {'assignmentId': 2})
        self.command(3, 'DELETE:exams_v2.assignment_detail', {'assignmentId': 2, 'outcome': 'deleted'})
        self.fixture.db.execute('DELETE FROM classic_exam_presented_questions WHERE attempt_id=2')
        self.fixture.db.execute('DELETE FROM classic_exam_attempts WHERE student_id=2')
        self.fixture.db.execute('DELETE FROM classic_exam_assignments WHERE student_id=2')
        self.fixture.db.commit()
        before = self.fixture.db.execute('SELECT * FROM exam_admin_commands ORDER BY id').fetchall()
        self.finish(self.start(1))
        self.assertEqual(self.fixture.db.execute('SELECT * FROM exam_admin_commands ORDER BY id').fetchall(), before)

    def test_missing_command_after_manifest_is_failed_not_false_success(self):
        self.command(1, 'PUT:exams_v2.student_privilege', {'studentId': 1}, student=1)
        job = self.start(); self.fixture.finish_homework(job)
        self.fixture.db.execute('DELETE FROM exam_admin_commands'); self.fixture.db.commit()
        for _ in range(3): self.fixture.due(); jobs.run_once()
        result = jobs.get_job(job['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], 'command_receipt_missing')
        self.assertLess(result['percent'], 100)
        self.assertIsNotNone(self.fixture.db.execute('SELECT id FROM students WHERE id=1').fetchone())

    def test_central_replay_rejects_tombstone_for_import_routes_too(self):
        request_key = str(uuid.uuid4())
        payload = {'expectedPreviewVersion': 1}
        with self.app.test_request_context('/api/exams/20/imports/assignments/sessions/1/commit', method='POST', headers={'Idempotency-Key': request_key}):
            row = {'request_hash': common.digest({'path': '/api/exams/20/imports/assignments/sessions/1/commit', 'method': 'POST', 'payload': payload}),
                   'expires_at': common.now()+timedelta(hours=1), 'tombstone': 1, 'receipt': None}
            db = Mock(); db.one.return_value = row
            with self.assertRaises(common.ExamError) as error:
                common.reserve_admin_command(db, self.actor, payload, 20)
            self.assertEqual(error.exception.code, 'removed_target')
            db.insert.assert_not_called()

    def test_login_only_excluded_and_error_previews_cannot_reintroduce_pending_student(self):
        for flags in ({}, {'excluded': True}, {'errors': [{'code': 'invalid_grade'}]}):
            with self.subTest(flags=flags):
                db = Mock()
                def lookup(sql, args=()):
                    if 'schema_version' in sql: return {'schema_version': 1}
                    if 'auth_users' in sql:
                        self.assertIn("role='student'", sql)
                        return {'ref_id': 1}
                    if 'student_deletion_barriers' in sql: return {'student_id': 1}
                    raise AssertionError(sql)
                db.one.side_effect = lookup
                row = dict({'rowId': 'row-2', 'input': {'studentLogin': 'current-login'}}, **flags)
                with patch('cpm_back.services.student_deletion.fence.protection_active', return_value=True):
                    with self.assertRaises(common.ExamError) as error:
                        imports.payload_json(db, [row])
                self.assertEqual(error.exception.code, 'student_deletion_pending')
                db.scalar.assert_not_called()

    def test_preview_producer_checks_resolved_identity_and_uses_fresh_current_login(self):
        db = Mock()
        db.scalar.return_value = 10**7
        checked = []
        def lookup(sql, args=()):
            if 'schema_version' in sql: return {'schema_version': 1}
            if 'auth_users' in sql: return {'ref_id': 2}
            checked.append(args[0])
            return None
        db.one.side_effect = lookup
        row = {'rowId': 'row-2', 'input': {'studentLogin': 'reused-login'}, 'resolved': {'student': {'id': 2}}}
        with patch('cpm_back.services.student_deletion.fence.protection_active', return_value=True):
            stored = json.loads(imports.payload_json(db, [row]))
        self.assertEqual(checked, [2])
        self.assertEqual(stored[0]['input']['studentLogin'], 'reused-login')
        self.assertNotIn('resolved', stored[0])

    def test_staff_login_numeric_id_collision_is_not_a_student_owner(self):
        db = Mock(); db.scalar.return_value = 10**7
        def lookup(sql, args=()):
            if 'schema_version' in sql: return {'schema_version': 1}
            if 'auth_users' in sql:
                self.assertIn("role='student'", sql)
                return None  # Same login belongs only to a staff account.
            self.fail('No student barrier lookup is justified for a staff-only login')
        db.one.side_effect = lookup
        row = {'rowId': 'row-2', 'excluded': True, 'input': {'studentLogin': 'staff-only-login'}}
        with patch('cpm_back.services.student_deletion.fence.protection_active', return_value=True):
            result = json.loads(imports.payload_json(db, [row]))
        self.assertTrue(result[0]['excluded'])

    def test_known_historical_login_only_reference_blocks_after_rename_without_erasing(self):
        self.fixture.db.execute("UPDATE auth_users SET username='new-login' WHERE ref_id=1")
        self.fixture.db.execute("UPDATE student_credentials SET login='new-login' WHERE student_id=1")
        self.fixture.db.execute("INSERT INTO user_import_jobs VALUES(5,8,'completed',NULL)")
        self.fixture.db.execute('INSERT INTO user_import_job_results VALUES(5,?)', (json.dumps([{'student_id': 1, 'row': 5, 'login': 'historic-login', 'full_name': 'Synthetic Target'}]),))
        self.fixture.db.commit()
        self.stored_preview([{'rowId': 'row-7', 'sourceRow': 7, 'excluded': True,
                             'input': {'studentLogin': 'historic-login'}}])
        preview = jobs.preview(1, self.actor)
        self.assertFalse(preview['available'])
        self.assertIn('сопоставить', preview['blockers'][0])
        self.assertEqual(self.fixture.db.execute('SELECT COUNT(*) FROM student_deletion_barriers').fetchone()[0], 0)

    def test_maintenance_excludes_durable_captured_command_ids(self):
        db = Mock()
        db.scalar.side_effect = lambda sql, *a, **k: ('synthetic' if 'DATABASE()' in sql and 'information_schema' not in sql else 1)
        db.all.return_value = []
        connection = Mock()
        with patch.object(maintenance, 'UnitOfWork', return_value=db):
            maintenance._sweep_expired(connection)
        receipt_selects = [call.args[0] for call in db.all.call_args_list if 'exam_admin_commands' in call.args[0]]
        self.assertEqual(len(receipt_selects), 1)
        self.assertIn("JSON_CONTAINS(JSON_EXTRACT(sdj.manifest_json,'$.command_ids')", receipt_selects[0])
        self.assertFalse(any('DELETE FROM exam_admin_commands' in str(call) for call in db.execute.call_args_list))
