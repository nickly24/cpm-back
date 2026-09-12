"""Offline authorization regression suite. Never creates the production application."""
import ast
from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask, jsonify
from cpm_back.auth.admin_permissions import (
    RULES, SECTIONS, allowed, enforce_admin_permissions, redact_directory_response,
)
from cpm_back.auth.jwt_auth import generate_token, require_role, verify_token
from cpm_back.blueprints.admin_access_bp import admin_access_bp
from cpm_back.services import admin_access as service
from cpm_back.services.exam.test_drafts import _owns_lock


def actor(section=None, edit=False):
    return {'role': 'staff_admin', 'id': 7, 'session_version': 1,
            'permissions': {section: {'view': True, 'edit': edit}} if section else {}}


class AdminRoleTests(unittest.TestCase):
    def setUp(self):
        # Both network and pool entry points fail loudly; all data below is synthetic.
        for target in ('socket.socket.connect', 'mysql.connector.connect',
                       'cpm_back.db.mysql_pool.get_db_connection'):
            patcher = patch(target, side_effect=AssertionError('Database/network access forbidden in this suite'))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, JWT_SECRET_KEY='offline-test-secret-for-admin-roles')
        self.app.before_request(enforce_admin_permissions)
        self.app.after_request(redact_directory_response)
        self.app.register_blueprint(admin_access_bp)
        self.handler = MagicMock(return_value={'status': True})
        for endpoint in RULES:
            def handler(current_user=None):
                return jsonify(self.handler())
            self.app.add_url_rule('/policy/' + endpoint, endpoint, require_role('admin')(handler), methods=['GET', 'POST', 'PUT', 'DELETE'])
        self.client = self.app.test_client()

    def call(self, user, endpoint, method='POST', payload=None):
        with patch('cpm_back.auth.jwt_auth.get_current_user', return_value=user):
            return self.client.open('/policy/' + endpoint, method=method, json=payload)

    def test_every_endpoint_requires_its_section_and_edit_is_not_view(self):
        for endpoint, requirements in RULES.items():
            payload = {'role': 'proctor'} if endpoint.startswith('users.') else {}
            with self.subTest(endpoint=endpoint):
                self.handler.reset_mock()
                self.assertEqual(self.call(actor(), endpoint, payload=payload).status_code, 403)
                self.handler.assert_not_called()
                for section, action in requirements:
                    self.assertEqual(self.call(actor(section, True), endpoint, payload=payload).status_code, 200)
                    expected = 200 if action == 'view' else 403
                    self.assertEqual(self.call(actor(section), endpoint, payload=payload).status_code, expected)

    def test_admin_routes_are_explicitly_classified(self):
        directory = Path(__file__).parents[1] / 'cpm_back/blueprints'
        for path in directory.glob('*_bp.py'):
            if path.name == 'admin_access_bp.py':
                continue  # Intentionally reserved to the original admin.
            tree = ast.parse(path.read_text())
            namespaces = {}
            for node in tree.body:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == 'Blueprint':
                    namespaces[node.targets[0].id] = node.value.args[0].value
            for node in tree.body:
                if not isinstance(node, ast.FunctionDef):
                    continue
                protected = any(isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in ('require_role', 'require_self_or_role') and any(isinstance(a, ast.Constant) and a.value == 'admin' for a in d.args) for d in node.decorator_list)
                if not protected:
                    continue
                for d in node.decorator_list:
                    if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and isinstance(d.func.value, ast.Name) and d.func.value.id in namespaces:
                        endpoint = namespaces[d.func.value.id] + '.' + node.name
                        self.assertIn(endpoint, RULES, path.name)

    def test_only_root_admin_can_manage_roles_and_accounts(self):
        paths = [('/api/admin-access', 'GET'), ('/api/admin-access/roles', 'POST'),
                 ('/api/admin-access/roles/1', 'PUT'), ('/api/admin-access/roles/1', 'DELETE'),
                 ('/api/admin-access/users', 'POST'), ('/api/admin-access/users/1', 'PUT'),
                 ('/api/admin-access/users/1', 'DELETE')]
        full = actor()
        full['permissions'] = {s: {'view': True, 'edit': True} for s in SECTIONS}
        for user, code in [(None, 401), ({'role': 'proctor'}, 403), (full, 403)]:
            with patch('cpm_back.auth.jwt_auth.get_current_user', return_value=user), patch.object(service, 'database', side_effect=AssertionError('Denied operation reached DB')):
                for path, method in paths:
                    self.assertEqual(self.client.open(path, method=method, json={}).status_code, code)
        with patch('cpm_back.auth.jwt_auth.get_current_user', return_value={'role': 'admin'}), patch.object(service, 'save_role', return_value={'id': 1, 'status': True}) as save:
            self.assertEqual(self.client.post('/api/admin-access/roles', json={'name': 'Offline role'}).status_code, 201)
            save.assert_called_once_with({'name': 'Offline role'})

    def test_unknown_routes_and_self_id_do_not_bypass_policy(self):
        with patch('cpm_back.auth.jwt_auth.get_current_user', return_value=actor('users', True)):
            self.assertEqual(self.client.post('/unknown', json={'student_id': 7}).status_code, 403)
        self.assertEqual(self.call(None, 'students.list_students').status_code, 401)

    def test_users_permission_cannot_administer_admins(self):
        for endpoint in [e for e in RULES if e.startswith('users.')]:
            for role in ('admin', 'staff_admin', 'admins', None, "admin' OR 1=1"):
                self.assertFalse(allowed(actor('users', True), endpoint, {'role': role}))

    def test_school_and_scanner_are_payload_scoped(self):
        school = actor('schools', True)
        self.assertTrue(allowed(school, 'students.edit', {'student_id': 3, 'school_id': 2}))
        self.assertFalse(allowed(school, 'students.edit', {'student_id': 3, 'school_id': 2, 'password': 'x'}))
        scanner = actor('scan', True)
        self.assertTrue(allowed(scanner, 'class_days.class_day_attendance_set', {'student_id': 3, 'attendance_type_id': 1}))
        for payload in ({'attendance_type_id': 2}, {'attendance_type_id': 1, 'zap_id': 2}):
            self.assertFalse(allowed(scanner, 'class_days.class_day_attendance_set', payload))

    def test_shared_directory_reads_hide_credentials(self):
        self.handler.return_value = {'students': [{'id': 2, 'full_name': 'Synthetic', 'login': 'secret', 'password': 'secret', 'tg_name': 'secret'}]}
        data = self.call(actor('schools'), 'students.list_students').json['students'][0]
        self.assertEqual(data, {'id': 2, 'full_name': 'Synthetic'})
        self.assertTrue(self.call(actor('users'), 'students.list_students').json['students'][0]['password_hidden'])
        self.assertIn('password', self.call(actor('users', True), 'students.list_students').json['students'][0])

    def test_bot_view_does_not_expose_control_token(self):
        self.handler.return_value = {'settings': {'bot_token': 'offline-secret', 'token_configured': True}}
        data = self.call(actor('telegram-bot'), 'telegram_bot.settings').json
        self.assertEqual(data['settings']['bot_token'], '')
        self.assertTrue(data['settings']['token_configured'])
        self.assertEqual(self.call(actor('telegram-bot', True), 'telegram_bot.settings').json['settings']['bot_token'], 'offline-secret')

    def test_live_token_permissions_and_revocation(self):
        user = actor('tests')
        with self.app.app_context():
            token = generate_token(user)
            with patch.object(service, 'load_staff_admin', return_value=user) as load:
                self.assertEqual(verify_token(token)['permissions'], user['permissions'])
                load.assert_called_once_with(7, 1)
                load.return_value = actor('schools')
                self.assertIn('schools', verify_token(token)['permissions'])
                load.return_value = None
                self.assertIsNone(verify_token(token))

    def test_permission_validation(self):
        self.assertEqual(service.normalize_permissions({'tests': {'edit': True}}), {'tests': {'view': True, 'edit': True}})
        for invalid in ([], {'access': {'view': True}}, {'tests': {'edit': 1}}, {'tests': {'delete': True}}):
            with self.assertRaises(ValueError):
                service.normalize_permissions(invalid)

    def test_create_user_hashes_password_and_only_writes_new_account_type(self):
        cursor = MagicMock(lastrowid=23)
        cursor.fetchone.return_value = {'id': 1}
        @contextmanager
        def fake_database(write=False):
            self.assertTrue(write)
            yield cursor
        with patch.object(service, 'database', fake_database):
            result = service.save_user({'full_name': 'Offline user', 'login': 'offline', 'role_id': 1})
        statements = cursor.execute.call_args_list
        self.assertEqual(len(statements), 3)
        sql, params = statements[-1].args
        self.assertIn("'staff_admin'", sql)
        self.assertNotEqual(params[1], result['credentials']['password'])
        from werkzeug.security import check_password_hash
        self.assertTrue(check_password_hash(params[1], result['credentials']['password']))

    def test_login_of_staff_requires_actual_password_and_loads_role(self):
        from importlib import import_module
        from werkzeug.security import generate_password_hash
        auth_module = import_module('cpm_back.auth.auth')
        hashed = generate_password_hash('offline-password')
        connection = MagicMock()
        connection.cursor.return_value.fetchone.return_value = {
            'password': hashed, 'ref_id': 7, 'role': 'staff_admin',
        }
        with patch.object(auth_module, 'get_db_connection', return_value=connection), patch.object(auth_module, 'close_db_connection'), patch.object(service, 'load_staff_admin', return_value=actor('tests')) as load:
            self.assertTrue(auth_module.auth('offline', 'offline-password')['status'])
            self.assertEqual(load.call_count, 1)
            self.assertFalse(auth_module.auth('offline', hashed)['status'])
            self.assertEqual(load.call_count, 1)
            load.return_value = None
            self.assertFalse(auth_module.auth('offline', 'offline-password')['status'])

    def test_live_loader_rejects_disabled_and_outdated_sessions(self):
        cursor = MagicMock()
        @contextmanager
        def fake_database(write=False):
            self.assertFalse(write)
            yield cursor
        with patch.object(service, 'database', fake_database):
            for row in (None, {'is_active': 0}, {'is_active': 1, 'session_version': 2}):
                cursor.fetchone.return_value = row
                self.assertIsNone(service.load_staff_admin(7, 1))
        self.assertTrue(all(call.args[0].startswith('SELECT') for call in cursor.execute.call_args_list))

    def test_role_delete_rejects_assigned_users(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [{'id': 1}, {'id': 2}]
        @contextmanager
        def fake_database(write=False):
            yield cursor
        with patch.object(service, 'database', fake_database), self.assertRaises(ValueError):
            service.delete_role(1)
        self.assertFalse(any('DELETE' in call.args[0] for call in cursor.execute.call_args_list))

    def test_viewing_expired_attempt_does_not_persist_status(self):
        from bson import ObjectId
        from cpm_back.services.exam import test_attempts
        collection = MagicMock()
        attempt_id = ObjectId()
        collection.find_one.return_value = {'_id': attempt_id, 'status': 'in_progress', 'expiresAt': '2020-01-01T00:00:00Z'}
        with patch.object(test_attempts, '_collection', return_value=collection):
            result = test_attempts.get_attempt_admin_detail(str(attempt_id), brief=True, read_only=True)
        self.assertTrue(result['attempt']['timeExpired'])
        collection.update_one.assert_not_called()
        collection.insert_one.assert_not_called()

    def test_numeric_ids_from_different_account_types_do_not_share_locks(self):
        self.assertTrue(_owns_lock({'lockedBy': 7}, {'role': 'admin', 'id': 7}))
        self.assertFalse(_owns_lock({'lockedBy': 7}, actor()))
        self.assertTrue(_owns_lock({'lockedBy': 7, 'lockedByRole': 'staff_admin'}, actor()))


if __name__ == '__main__':
    unittest.main()
