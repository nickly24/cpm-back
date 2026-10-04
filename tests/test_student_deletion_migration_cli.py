"""The delivery CLI has explicit credentials/confirmation and read-only preflight."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('student_deletion_schema_cli', Path(__file__).resolve().parents[1] / 'scripts/student_deletion_schema.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class MigrationCliTests(unittest.TestCase):
    def test_sql_splitter_preserves_procedure_body(self):
        statements = list(cli.statements("-- comment\nCREATE TABLE t(id INT);\nDELIMITER //\nCREATE PROCEDURE p()\nBEGIN\nSELECT 1;\nEND//\nDELIMITER ;\nCALL p();"))
        self.assertEqual(len(statements), 3)
        self.assertIn('SELECT 1;', statements[1])

    def test_unterminated_script_rejected(self):
        with self.assertRaises(ValueError): list(cli.statements('SELECT 1'))

    def test_remote_and_mutating_modes_require_explicit_approval_before_connection(self):
        for args in (['preflight', '--host', 'example.invalid'], ['preflight', '--host', 'example.invalid', '--allow-remote'], ['base'], ['homework'], ['apply']):
            with self.subTest(args=args), patch.dict('os.environ', {'MYSQL_PASSWORD': 'synthetic'}), patch('mysql.connector.connect') as connect:
                with self.assertRaises(SystemExit): cli.main(args + ['--database', 'synthetic', '--user', 'synthetic'])
                connect.assert_not_called()

    def test_missing_password_does_not_fall_back_to_repository_config(self):
        with patch.dict('os.environ', {}, clear=True), patch('mysql.connector.connect') as connect:
            with self.assertRaises(SystemExit): cli.main(['preflight', '--database', 'synthetic', '--user', 'synthetic'])
            connect.assert_not_called()

    def test_audited_baseline_can_be_checked_before_additive_migrations_exist(self):
        import json
        from cpm_back.services.student_deletion import fence
        baseline = json.loads((cli.ROOT / 'migrations/student-deletion-schema-audit-v2/expected-baseline.json').read_text())
        columns = {}
        for row in baseline['columns']:
            columns.setdefault(row['TABLE_NAME'], {})[row['COLUMN_NAME']] = row['DATA_TYPE']
        engines = {row['TABLE_NAME']: row['ENGINE'] for row in baseline['tables']}
        prepared, prepared_engines = cli.prepared_metadata(columns, engines)
        self.assertNotIn('processed_output_key', columns['homework_file_jobs'])
        self.assertIn('processed_output_key', prepared['homework_file_jobs'])
        self.assertNotIn('student_deletion_jobs', columns)
        self.assertTrue(fence._validate_ownership_schema(prepared, baseline['foreign_keys'], prepared_engines))

    def test_preflight_is_read_only_and_does_not_modify_metadata_input(self):
        required = {
            'students': ('id', 'full_name', 'group_id'), 'auth_users': ('role', 'ref_id', 'username'),
            'groups': ('id', 'name'), 'homework_sessions': ('student_id', 'homework_id'),
            'exam_sessions': ('student_id', 'exam_id'), 'test_sessions': ('student_id', 'test_id'),
            'homework_submissions': ('id', 'student_id', 'homework_id'),
            'homework_submission_files': ('id', 'submission_id', 'object_key'),
            'homework_file_jobs': ('id', 'student_id', 'submission_id', 'staging_key'),
            'homework_s3_delete_queue': ('object_key', 'status', 'attempts', 'available_at'),
        }
        columns = {table: {name: 'int' for name in fields} for table, fields in required.items()}
        fence = Mock(); fence._read_schema.return_value = columns, [], {table: 'InnoDB' for table in columns}
        fence._validate_ownership_schema.return_value = set(columns)
        connection = Mock(); cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [{'version': '8.0.46'}, {'count': 0}, {'count': 0}, {'count': 0}]
        cli.preflight(connection, fence)
        self.assertNotIn('student_deletion_barriers', columns)
        self.assertTrue(all(call.args[0].lstrip().startswith('SELECT') for call in cursor.execute.call_args_list))
        connection.commit.assert_not_called(); connection.rollback.assert_called_once()
