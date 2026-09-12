"""Offline regressions for legacy homework scope and file workflow isolation."""
import importlib
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask
from mysql.connector.errors import ProgrammingError
from cpm_back.blueprints.homework_bp import homework_bp
from cpm_back.services.serv.homework_access import (
    HomeworkAccessError, attach_file_submissions, ensure_legacy_editable, scoped_proctor_id,
)


class HomeworkBoundaryTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'mysql.connector.connect'):
            guard = patch(target, side_effect=AssertionError('Live network is forbidden'))
            guard.start()
            self.addCleanup(guard.stop)
        self.actor = {'role': 'proctor', 'id': 7}
        self.app = Flask(__name__)
        self.app.register_blueprint(homework_bp)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def post(self, route, payload):
        with patch('cpm_back.auth.jwt_auth.get_current_user', return_value=self.actor):
            return self.client.post('/api/' + route, json=payload)

    def test_read_and_bulk_reject_foreign_proctor_before_db(self):
        for route, body in [
            ('get-homework-sessions', {'proctorId': 99, 'homeworkId': 2}),
            ('pass_homework_bulk', {'proctorId': 99, 'homeworkId': 2, 'datePass': '2026-09-12'}),
        ]:
            response = self.post(route, body)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json['error'], 'student_not_in_current_group')

    def test_proctor_id_is_derived_from_actor_when_omitted(self):
        self.assertEqual(scoped_proctor_id(self.actor, None), 7)
        self.assertEqual(scoped_proctor_id(self.actor, '7'), 7)

    def test_pass_and_reset_reject_foreign_student_without_session_mutation(self):
        for module_name, route, body, answers in [
            ('pass_homework', 'pass_homework', {'sessionId': 8, 'datePass': '2026-09-12'},
             [{'homework_id': 2, 'student_id': 3}, {'deadline': __import__('datetime').date(2026, 9, 12)}, None]),
            ('edit_homework_session', 'edit-homework-session', {'sessionId': 8, 'status': 0},
             [{'homework_id': 2, 'student_id': 3}, None]),
        ]:
            module = importlib.import_module('cpm_back.services.serv.' + module_name)
            conn, cursor = MagicMock(), MagicMock()
            conn.cursor.return_value = cursor
            cursor.fetchone.side_effect = answers
            with patch.object(module, 'get_db_connection', return_value=conn), patch.object(module, 'close_db_connection'):
                response = self.post(route, body)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json['error'], 'student_not_in_current_group')
            conn.commit.assert_not_called()
            self.assertFalse(any('UPDATE homework_sessions' in call.args[0] for call in cursor.execute.call_args_list))

    def test_file_managed_reset_is_conflict_and_rolls_back(self):
        module = importlib.import_module('cpm_back.services.serv.edit_homework_session')
        conn, cursor = MagicMock(), MagicMock()
        conn.cursor.return_value = cursor
        cursor.fetchone.side_effect = [
            {'homework_id': 2, 'student_id': 3}, {'id': 3},
            {'state': 'graded', 'current_file_id': 20, 'draft_file_id': None},
        ]
        with patch.object(module, 'get_db_connection', return_value=conn), patch.object(module, 'close_db_connection'):
            response = self.post('edit-homework-session', {'sessionId': 8, 'status': 0})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['error'], 'use_file_review')
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
        self.assertFalse(any('UPDATE homework_sessions' in call.args[0] for call in cursor.execute.call_args_list))

    def test_all_file_states_are_protected_and_none_remains_legacy(self):
        for state in ('uploading', 'processing', 'draft', 'submitted', 'in_review', 'revision_requested', 'graded'):
            cursor = MagicMock()
            cursor.fetchone.return_value = {'state': state, 'current_file_id': None, 'draft_file_id': None}
            with self.assertRaises(HomeworkAccessError):
                ensure_legacy_editable(cursor, 2, 3)
        cursor.fetchone.return_value['state'] = 'none'
        ensure_legacy_editable(cursor, 2, 3)

    def test_optional_migration_fallback_does_not_hide_other_errors(self):
        cursor = MagicMock()
        cursor.execute.side_effect = ProgrammingError('missing table', errno=1146)
        ensure_legacy_editable(cursor, 2, 3)
        rows = [{'homework_id': 2}]
        attach_file_submissions(cursor, rows, student_id=3)
        self.assertFalse(rows[0]['file_managed'])
        self.assertIsNone(rows[0]['submission_state'])
        cursor.execute.side_effect = ProgrammingError('permission denied', errno=1142)
        with self.assertRaises(ProgrammingError):
            attach_file_submissions(cursor, rows, student_id=3)

    def test_list_includes_file_state_without_overwriting_legacy_result(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{'id': 8, 'homework_id': 2, 'student_id': 3,
            'state': 'submitted', 'current_file_id': 20, 'draft_file_id': None,
            'submitted_at_utc': None, 'revision_comment': None}]
        rows = [{'student_id': 3, 'status': 0, 'result': None}]
        attach_file_submissions(cursor, rows, homework_id='2')
        self.assertTrue(rows[0]['file_managed'])
        self.assertEqual(rows[0]['submission_state'], 'submitted')
        self.assertEqual(rows[0]['status'], 0)


if __name__ == '__main__':
    unittest.main()
