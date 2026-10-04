"""Offline synthetic coordinator contract tests, not a MySQL/Mongo integration claim.

SQLite is used only by this driver fake; SQL lock/trigger semantics are tested
separately by the real MySQL engine harness. No create_app or real pool is used.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import re
import sqlite3
import unittest
from unittest.mock import patch
import uuid

import mongomock
from flask import Flask
from itsdangerous import SignatureExpired

from cpm_back.blueprints.student_deletion_bp import student_deletion_bp
from cpm_back.services.student_deletion import jobs, inventory, retention
from cpm_back.services.student_deletion.common import DeletionError
from cpm_back.services.serv.delete_user import delete_user


@contextmanager
def fake_fence(*args, **kwargs):
    yield None


class Cursor:
    def __init__(self, fixture):
        self.fixture = fixture
        self.cursor = fixture.db.cursor()
        self.result = None
        self.rowcount = 0

    def execute(self, sql, params=()):
        self.result = None
        if 'information_schema.COLUMNS' in sql:
            self.result = []
            for name, in self.fixture.db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                for col in self.fixture.db.execute(f'PRAGMA table_info(`{name}`)'):
                    self.result.append({'TABLE_NAME': name, 'COLUMN_NAME': col[1], 'COLUMN_KEY': 'PRI' if col[5] else '', 'ORDINAL_POSITION': col[0]})
            return
        if 'information_schema.KEY_COLUMN_USAGE' in sql:
            self.result = self.fixture.foreign_keys
            return
        if sql.startswith('SHOW TABLES LIKE'):
            name = params[0] if params else sql.split("'")[1]
            self.result = [{'table': name}] if self.fixture.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() else []
            return
        sql = re.sub(r'\s+FOR UPDATE\b', '', sql, flags=re.I)
        sql = sql.replace('DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 60 SECOND)', "datetime('now','-60 seconds')")
        sql = sql.replace('UTC_TIMESTAMP(6)', "strftime('%Y-%m-%d %H:%M:%f','now')")
        sql = sql.replace('%s', '?').replace(' AS BINARY)', ' AS BLOB)')
        self.cursor.execute(sql, tuple(value.isoformat(sep=' ') if isinstance(value, datetime) else value for value in params))
        self.rowcount = self.cursor.rowcount

    def _row(self, row):
        if row is None:
            return None
        result = dict(zip([c[0] for c in self.cursor.description], row))
        for key, value in result.items():
            if isinstance(value, str) and (key.endswith('_at') or key == 'wait_until'):
                result[key] = datetime.fromisoformat(value)
        return result

    def fetchone(self):
        if self.result is not None:
            return self.result.pop(0) if self.result else None
        return self._row(self.cursor.fetchone())

    def fetchall(self):
        if self.result is not None:
            result, self.result = self.result, []
            return result
        return [self._row(row) for row in self.cursor.fetchall()]

    def close(self):
        self.cursor.close()


class Connection:
    def __init__(self, fixture):
        self.fixture = fixture

    def cursor(self, **kwargs):
        return Cursor(self.fixture)

    def start_transaction(self, **kwargs):
        self.fixture.db.execute('BEGIN')

    def commit(self):
        self.fixture.db.commit()

    def rollback(self):
        self.fixture.db.rollback()


class Fixture:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.foreign_keys = []
        self.db.executescript('''
        CREATE TABLE students(id INTEGER PRIMARY KEY,full_name TEXT,group_id INTEGER);
        CREATE TABLE `groups`(id INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE auth_users(id INTEGER PRIMARY KEY,role TEXT,ref_id INTEGER,username TEXT,password TEXT);
        CREATE TABLE student_credentials(student_id INTEGER PRIMARY KEY,login TEXT,password TEXT);
        CREATE TABLE homework_sessions(id INTEGER PRIMARY KEY,student_id INTEGER,homework_id INTEGER,result INTEGER);
        CREATE TABLE exam_sessions(id INTEGER PRIMARY KEY,student_id INTEGER,exam_id INTEGER,result INTEGER);
        CREATE TABLE test_sessions(id INTEGER PRIMARY KEY,student_id INTEGER,test_id INTEGER,rate INTEGER);
        CREATE TABLE homework_submissions(id INTEGER PRIMARY KEY,student_id INTEGER,homework_id INTEGER,current_file_id INTEGER,draft_file_id INTEGER);
        CREATE TABLE homework_submission_files(id INTEGER PRIMARY KEY,submission_id INTEGER,object_key TEXT);
        CREATE TABLE homework_file_jobs(id TEXT PRIMARY KEY,submission_id INTEGER,student_id INTEGER,staging_key TEXT,processed_output_key TEXT,homework_id INTEGER,result_file_id INTEGER);
        CREATE TABLE homework_object_ownership(object_key TEXT PRIMARY KEY,student_id INTEGER,submission_id INTEGER,upload_job_id TEXT);
        CREATE TABLE homework_service_capabilities(service_name TEXT PRIMARY KEY,protocol_version INTEGER,heartbeat_at TEXT);
        CREATE TABLE homework_student_purges(job_id TEXT PRIMARY KEY,student_id INTEGER,status TEXT DEFAULT 'queued',total_units INTEGER DEFAULT 0,completed_units INTEGER DEFAULT 0,error_code TEXT,available_at TEXT DEFAULT CURRENT_TIMESTAMP,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE homework_student_purge_objects(job_id TEXT,object_key TEXT,PRIMARY KEY(job_id,object_key));
        CREATE TABLE student_deletion_barriers(student_id INTEGER PRIMARY KEY,job_id TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE student_deletion_jobs(id TEXT PRIMARY KEY,student_id INTEGER UNIQUE,actor_role TEXT,actor_id INTEGER,idempotency_key TEXT,confirmation_hash TEXT,fingerprint TEXT,student_json TEXT,manifest_json TEXT,status TEXT DEFAULT 'queued',stage TEXT DEFAULT 'homework',completed_units INTEGER DEFAULT 0,total_units INTEGER,lease_owner TEXT,lease_expires_at TEXT,available_at TEXT DEFAULT CURRENT_TIMESTAMP,attempts INTEGER DEFAULT 0,error_code TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,completed_at TEXT);
        CREATE TABLE classic_exam_assignments(id INTEGER PRIMARY KEY,student_id INTEGER,exam_id INTEGER);
        CREATE TABLE classic_exam_attempts(id INTEGER PRIMARY KEY,student_id INTEGER,exam_id INTEGER,assignment_id INTEGER,calculated_grade INTEGER);
        CREATE TABLE classic_exam_presented_questions(id INTEGER PRIMARY KEY,attempt_id INTEGER);
        CREATE TABLE attendance(id INTEGER PRIMARY KEY,student_id INTEGER,value TEXT);
        CREATE TABLE exams(id INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE homework(id INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE tests_out(id INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE exam_admin_commands(id INTEGER PRIMARY KEY,actor_role TEXT DEFAULT 'admin',actor_id INTEGER DEFAULT 90,idempotency_key TEXT UNIQUE,command_type TEXT,target_exam_id INTEGER,target_student_id INTEGER,target_attempt_id INTEGER,request_hash TEXT DEFAULT 'synthetic-hash',receipt TEXT,tombstone INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP,expires_at TEXT DEFAULT '2099-01-01');
        CREATE TABLE user_import_jobs(id INTEGER PRIMARY KEY,session_id INTEGER,status TEXT,entities_created TEXT);
        CREATE TABLE user_import_job_results(job_id INTEGER PRIMARY KEY,rows_data TEXT);
        CREATE TABLE user_import_sessions(id INTEGER PRIMARY KEY,preview_payload TEXT);
        INSERT INTO `groups` VALUES(1,'Synthetic group');
        INSERT INTO students VALUES(1,'Synthetic Target',1),(2,'Synthetic Other',1),(3,'Synthetic Empty',NULL);
        INSERT INTO auth_users VALUES(1,'student',1,'synth1','synthetic'),(2,'student',2,'synth2','synthetic'),(3,'student',3,'synth3','synthetic');
        INSERT INTO student_credentials VALUES(1,'synth1','synthetic');
        INSERT INTO homework VALUES(10,'Shared homework'); INSERT INTO exams VALUES(20,'Shared exam'); INSERT INTO tests_out VALUES(30,'Shared external test');
        INSERT INTO homework_sessions VALUES(1,1,10,80),(2,2,10,90);
        INSERT INTO homework_submissions VALUES(1,1,10,1,NULL),(2,2,10,2,NULL);
        INSERT INTO homework_submission_files VALUES(1,1,'processed/one'),(2,2,'processed/two');
        INSERT INTO homework_file_jobs VALUES('upload1',1,1,'staging/one',NULL,10,NULL);
        INSERT INTO homework_object_ownership VALUES('processed/one',1,1,NULL),('staging/one',1,1,'upload1'),('processed/two',2,2,NULL);
        INSERT INTO exam_sessions VALUES(1,1,20,4),(2,2,20,5);
        INSERT INTO test_sessions VALUES(1,1,30,70),(2,2,30,80);
        INSERT INTO classic_exam_assignments VALUES(1,1,20),(2,2,20);
        INSERT INTO classic_exam_attempts VALUES(1,1,20,1,4),(2,2,20,2,5);
        INSERT INTO classic_exam_presented_questions VALUES(1,1),(2,2);
        INSERT INTO attendance VALUES(1,1,'present'),(2,2,'present');
        INSERT INTO homework_service_capabilities VALUES('student-purge',1,CURRENT_TIMESTAMP);
        ''')
        self.db.commit()
        self.mongo = mongomock.MongoClient().test_db
        self.mongo.tests.insert_one({'_id': 'shared-test', 'title': 'Shared internal test'})
        for sid in (1, 2):
            self.mongo.test_attempts.insert_one({'_id': f'attempt{sid}', 'studentId': str(sid), 'testId': 'shared-test', 'answers': [sid]})
            self.mongo.test_sessions.insert_one({'_id': f'session{sid}', 'studentId': sid, 'testId': 'shared-test', 'attemptId': f'attempt{sid}', 'score': 90})
            self.mongo.rate_rec.insert_one({'_id': f'rating{sid}', 'student_id': sid, 'detail': [sid]})

    def connection(self):
        return Connection(self)

    def finish_homework(self, job):
        sid = job['student_id']
        manifest = json.loads(self.db.execute('SELECT manifest_json FROM student_deletion_jobs WHERE id=?', (job['id'],)).fetchone()[0])
        self.db.execute('DELETE FROM homework_submission_files WHERE submission_id IN (SELECT id FROM homework_submissions WHERE student_id=?)', (sid,))
        self.db.execute('DELETE FROM homework_file_jobs WHERE student_id=?', (sid,))
        self.db.execute('DELETE FROM homework_submissions WHERE student_id=?', (sid,))
        self.db.execute("UPDATE homework_student_purges SET status='completed',total_units=?,completed_units=? WHERE job_id=?", (manifest['homework_units'], manifest['homework_units'], job['id']))
        self.db.commit()

    def due(self):
        self.db.execute("UPDATE student_deletion_jobs SET available_at='2000-01-01'")
        self.db.commit()


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, JWT_SECRET_KEY='synthetic-unit-secret')
        self.app.register_blueprint(student_deletion_bp)
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.actor = {'role': 'admin', 'id': 90}
        patches = [patch('cpm_back.services.student_deletion.common.get_db_connection', self.fixture.connection),
                   patch('cpm_back.services.student_deletion.common.close_db_connection', lambda c: None),
                   patch.object(jobs, 'get_db_connection', self.fixture.connection),
                   patch.object(jobs, 'close_db_connection', lambda c: None),
                   patch.object(jobs, 'get_mongo_db', lambda: self.fixture.mongo),
                   patch.object(jobs, 'mutation_fence', fake_fence),
                   patch('cpm_back.blueprints.student_deletion_bp.get_current_user', lambda: self.actor),
                   patch.dict('os.environ', {'STUDENT_DELETION_ENABLED': 'true'})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def start(self, sid=1):
        view = jobs.preview(sid, self.actor)
        self.assertTrue(view['available'], view['blockers'])
        return jobs.start(sid, self.actor, view['confirmation_token'], str(uuid.uuid4()))

    def test_exact_preview_no_test_or_exam_double_count(self):
        view = jobs.preview(1, self.actor)
        self.assertTrue(view['available'], view['blockers'])
        self.assertEqual(view['student']['name'], 'Synthetic Target')
        for key, expected in {'homeworks': 1, 'homework_submissions': 1, 'homework_files': 2, 'exams': 1, 'exam_attempts': 1, 'exam_results': 2, 'tests': 2, 'test_attempts': 1, 'test_results': 2}.items():
            self.assertEqual(view['counts'][key], expected, key)
        self.assertIsNotNone(view['confirmation_token'])

    def test_full_synthetic_pipeline_preserves_other_student_and_shared_definitions(self):
        job = self.start()
        self.assertEqual(self.fixture.db.execute('SELECT COUNT(*) FROM student_deletion_barriers').fetchone()[0], 1)
        self.fixture.finish_homework(job)
        for _ in range(4):
            self.fixture.due()
            self.assertTrue(jobs.run_once())
        final = jobs.get_job(job['id'])
        self.assertEqual(final['status'], 'completed', final)
        self.assertEqual(final['percent'], 100)
        self.assertEqual(final['completed_units'], final['total_units'])
        self.assertEqual(self.fixture.db.execute('SELECT id FROM students ORDER BY id').fetchall(), [(2,), (3,)])
        for table in ('homework', 'exams', 'tests_out'):
            self.assertEqual(self.fixture.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 1)
        for table in ('homework_sessions', 'exam_sessions', 'test_sessions', 'classic_exam_attempts', 'attendance'):
            self.assertEqual(self.fixture.db.execute(f'SELECT student_id FROM {table}').fetchall(), [(2,)])
        self.assertEqual(self.fixture.mongo.test_attempts.count_documents({}), 1)
        self.assertEqual(self.fixture.mongo.test_sessions.count_documents({}), 1)
        self.assertEqual(self.fixture.mongo.tests.count_documents({}), 1)
        self.assertEqual(self.fixture.db.execute('SELECT COUNT(*) FROM student_deletion_barriers').fetchone()[0], 1)
        self.assertEqual(jobs.list_jobs(1)[0]['id'], job['id'])

    def test_empty_student_still_verifies_all_stores(self):
        job = self.start(3)
        self.fixture.finish_homework(job)
        for _ in range(4):
            self.fixture.due(); jobs.run_once()
        self.assertEqual(jobs.get_job(job['id'])['status'], 'completed')

    def test_required_source_unavailable_is_unknown_and_no_token(self):
        with patch.object(jobs, 'get_mongo_db', side_effect=RuntimeError('offline')):
            view = jobs.preview(1, self.actor)
        self.assertFalse(view['available'])
        self.assertIsNone(view['counts']['tests'])
        self.assertIsNone(view['confirmation_token'])
        self.assertTrue(view['blockers'])

    def test_worker_readiness_expired_blocks_preview(self):
        self.fixture.db.execute("UPDATE homework_service_capabilities SET heartbeat_at='2000-01-01'")
        self.fixture.db.commit()
        self.assertFalse(jobs.preview(1, self.actor)['available'])

    def test_stale_preview_rolls_back_barrier_before_any_delete(self):
        view = jobs.preview(1, self.actor)
        self.fixture.db.execute("UPDATE attendance SET value='absent' WHERE student_id=1"); self.fixture.db.commit()
        with self.assertRaises(DeletionError) as context:
            jobs.start(1, self.actor, view['confirmation_token'], str(uuid.uuid4()))
        self.assertEqual(context.exception.code, 'preview_changed')
        self.assertEqual(self.fixture.db.execute('SELECT COUNT(*) FROM student_deletion_barriers').fetchone()[0], 0)
        self.assertEqual(self.fixture.db.execute('SELECT COUNT(*) FROM students').fetchone()[0], 3)

    def test_actor_student_expiry_binding(self):
        view = jobs.preview(1, self.actor)
        for sid, actor in ((2, self.actor), (1, {'role': 'admin', 'id': 91})):
            with self.assertRaises(DeletionError) as context:
                jobs.validate_confirmation(view['confirmation_token'], sid, actor)
            self.assertEqual(context.exception.code, 'invalid_confirmation')
        with patch.object(jobs, 'serializer') as factory:
            factory.return_value.loads.side_effect = SignatureExpired('old')
            with self.assertRaises(DeletionError) as context:
                jobs.validate_confirmation(view['confirmation_token'], 1, self.actor)
            self.assertEqual(context.exception.code, 'confirmation_expired')

    def test_double_click_and_lost_response_replay_same_job(self):
        view = jobs.preview(1, self.actor); key = str(uuid.uuid4())
        a = jobs.start(1, self.actor, view['confirmation_token'], key)
        b = jobs.start(1, self.actor, view['confirmation_token'], key)
        self.assertEqual(a['id'], b['id'])
        self.assertEqual(jobs.preview(1, self.actor)['active_job']['id'], a['id'])
        with self.assertRaises(DeletionError) as context:
            jobs.start(2, self.actor, view['confirmation_token'], key)
        self.assertEqual(context.exception.code, 'idempotency_conflict')

    def test_crash_after_mongo_delete_before_checkpoint_retries_idempotently(self):
        job = self.start(); self.fixture.finish_homework(job); jobs.run_once()
        self.fixture.due()
        with patch.object(jobs, '_checkpoint', side_effect=RuntimeError('synthetic crash')):
            jobs.run_once()
        failed = jobs.get_job(job['id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertLess(failed['percent'], 100)
        self.assertEqual(self.fixture.mongo.test_attempts.count_documents({'studentId': '1'}), 0)
        self.assertIsNotNone(jobs.retry(job['id']))
        for _ in range(3):
            self.fixture.due(); jobs.run_once()
        self.assertEqual(jobs.get_job(job['id'])['status'], 'completed')

    def test_expired_lease_is_resumed_after_redeployment(self):
        job = self.start(); self.fixture.finish_homework(job)
        self.fixture.db.execute("UPDATE student_deletion_jobs SET status='running',lease_owner='old',lease_expires_at='2000-01-01'"); self.fixture.db.commit()
        self.assertTrue(jobs.run_once())
        self.assertEqual(jobs.get_job(job['id'])['stage'], 'mongo')

    def test_waiting_progress_does_not_inflate_total_or_reach_100(self):
        job = self.start()
        self.fixture.db.execute("UPDATE homework_student_purges SET status='waiting',total_units=5,completed_units=3"); self.fixture.db.commit()
        jobs.run_once(); first = jobs.get_job(job['id'])
        self.fixture.due(); jobs.run_once(); second = jobs.get_job(job['id'])
        self.assertEqual(first['total_units'], second['total_units'])
        self.assertEqual(second['completed_units'], 3)
        self.assertLess(second['percent'], 100)

    def test_unknown_student_schema_fails_closed(self):
        self.fixture.db.execute('CREATE TABLE surprise_data(id INTEGER PRIMARY KEY,student_id INTEGER)'); self.fixture.db.commit()
        view = jobs.preview(1, self.actor)
        self.assertFalse(view['available']); self.assertIn('surprise_data', view['blockers'][0])

    def test_unknown_mongo_owner_layout_fails_closed(self):
        self.fixture.mongo.legacy_answers.insert_one({'student_id': 1, 'answer': 'synthetic'})
        self.assertFalse(jobs.preview(1, self.actor)['available'])
        self.fixture.mongo.legacy_answers.delete_many({})
        self.fixture.mongo.test_sessions.insert_one({'student_id': 1, 'testId': 'legacy'})
        self.assertFalse(jobs.preview(1, self.actor)['available'])

    def test_active_import_blocks_start_and_preview(self):
        self.fixture.db.execute("INSERT INTO user_import_jobs VALUES(1,1,'running',NULL)"); self.fixture.db.commit()
        self.assertFalse(jobs.preview(1, self.actor)['available'])

    def test_legacy_route_never_opens_database(self):
        with patch('cpm_back.services.serv.delete_user.get_db_connection', side_effect=AssertionError('must not open')):
            response = delete_user('student', 1)
        self.assertEqual(response['code'], 'student_deletion_workflow_required')
        self.assertEqual(response['http_status'], 409)

    def test_api_all_endpoints_require_users_edit(self):
        client = self.app.test_client()
        job_id = str(uuid.uuid4())
        paths = [('/api/students/1/deletion-preview', 'get'), ('/api/students/1/deletion-jobs', 'get'), ('/api/students/1/deletion-jobs', 'post'), (f'/api/student-deletion-jobs/{job_id}', 'get'), (f'/api/student-deletion-jobs/{job_id}/retry', 'post')]
        for actor, expected in ((None, 401), ({'role': 'student', 'id': 1}, 403), ({'role': 'staff_admin', 'id': 8, 'permissions': {'users': {'view': True}}}, 403)):
            self.actor = actor
            for path, method in paths:
                self.assertEqual(getattr(client, method)(path, json={}).status_code, expected)
        self.actor = {'role': 'staff_admin', 'id': 8, 'permissions': {'users': {'edit': True}}}
        self.assertEqual(client.get(paths[0][0]).status_code, 200)

    def test_import_rows_scrubbed_without_touching_shared_batch(self):
        target = {'row': 1, 'student_id': 1, 'full_name': 'Synthetic Target', 'password': 'synthetic'}
        other = {'row': 2, 'student_id': 2, 'full_name': 'Synthetic Other', 'password': 'synthetic'}
        self.fixture.db.execute("INSERT INTO user_import_jobs VALUES(5,8,'completed',?)", (json.dumps({'student_ids_created': [1, 2]}),))
        self.fixture.db.execute('INSERT INTO user_import_job_results VALUES(5,?)', (json.dumps([target, other]),))
        self.fixture.db.execute('INSERT INTO user_import_sessions VALUES(8,?)', (json.dumps({'students': [{'row': 1, 'full_name': 'Synthetic Target'}, {'row': 2, 'full_name': 'Synthetic Other'}]}),))
        self.fixture.db.commit()
        job = self.start(); self.fixture.finish_homework(job)
        for _ in range(4): self.fixture.due(); jobs.run_once()
        self.assertEqual(jobs.get_job(job['id'])['status'], 'completed')
        results = json.loads(self.fixture.db.execute('SELECT rows_data FROM user_import_job_results').fetchone()[0])
        self.assertEqual(results, [other])
        self.assertEqual(len(json.loads(self.fixture.db.execute('SELECT preview_payload FROM user_import_sessions').fetchone()[0])['students']), 1)

    def test_exam_receipt_sanitized_to_permanent_idempotency_tombstone(self):
        self.fixture.db.execute("INSERT INTO exam_admin_commands(id,idempotency_key,command_type,target_student_id,receipt) VALUES(1,'request-one','PUT:exams_v2.student_privilege',1,?)", (json.dumps({'studentId': 1}),))
        self.fixture.db.execute("INSERT INTO exam_admin_commands(id,idempotency_key,command_type,target_student_id,receipt) VALUES(2,'request-two','PUT:exams_v2.student_privilege',2,?)", (json.dumps({'studentId': 2}),))
        self.fixture.db.commit()
        job = self.start()
        manifest = json.loads(self.fixture.db.execute('SELECT manifest_json FROM student_deletion_jobs').fetchone()[0])
        self.assertNotIn('exam_admin_commands', manifest['sql'])
        self.assertEqual(manifest['command_ids'], [1])
        # Mirror the invariant: the receipt is never deleted or edited into new content.
        self.fixture.db.execute("CREATE TRIGGER deny_owned_receipt_delete BEFORE DELETE ON exam_admin_commands WHEN OLD.id=1 BEGIN SELECT RAISE(ABORT,'protected receipt'); END")
        self.fixture.db.execute("""CREATE TRIGGER permit_only_sanitization BEFORE UPDATE ON exam_admin_commands
          WHEN OLD.id=1 AND NOT(NEW.tombstone=1 AND NEW.receipt IS NULL AND NEW.target_student_id IS NULL AND NEW.target_attempt_id IS NULL AND NEW.request_hash=OLD.request_hash AND NEW.idempotency_key=OLD.idempotency_key)
          BEGIN SELECT RAISE(ABORT,'pending'); END""")
        self.fixture.db.commit()
        self.fixture.finish_homework(job)
        for _ in range(4): self.fixture.due(); jobs.run_once()
        self.assertEqual(jobs.get_job(job['id'])['status'], 'completed')
        self.assertEqual(self.fixture.db.execute('SELECT id,idempotency_key,tombstone,receipt,target_student_id,target_attempt_id FROM exam_admin_commands WHERE id=1').fetchone(), (1,'request-one',1,None,None,None))
        self.assertEqual(json.loads(self.fixture.db.execute('SELECT receipt FROM exam_admin_commands WHERE id=2').fetchone()[0]), {'studentId': 2})
        self.assertEqual(json.loads(self.fixture.db.execute('SELECT manifest_json FROM student_deletion_jobs').fetchone()[0])['command_ids'], [1])

    def test_staging_only_upload_is_counted_in_preview(self):
        self.fixture.db.execute("UPDATE homework_submissions SET current_file_id=NULL WHERE id=1")
        self.fixture.db.execute("DELETE FROM homework_submission_files WHERE submission_id=1")
        self.fixture.db.execute("DELETE FROM homework_object_ownership WHERE object_key='processed/one'")
        self.fixture.db.commit()
        preview = jobs.preview(1, self.actor)
        self.assertEqual(preview['counts']['homework_files'], 1)
        detail = {item['key']: item['count'] for item in preview['details']}
        self.assertEqual(detail['homework_file_records'], 0)
        self.assertEqual(detail['homework_storage_objects'], 1)

    def test_two_accepted_deletions_scrub_shared_import_atomically(self):
        payload = [{'student_id': 1, 'row': 1, 'full_name': 'Synthetic Target'}, {'student_id': 2, 'row': 2, 'full_name': 'Synthetic Other'}]
        self.fixture.db.execute("INSERT INTO user_import_jobs VALUES(5,8,'completed',?)", (json.dumps({'student_ids_created': [1, 2]}),))
        self.fixture.db.execute('INSERT INTO user_import_job_results VALUES(5,?)', (json.dumps(payload),))
        self.fixture.db.execute('INSERT INTO user_import_sessions VALUES(8,?)', (json.dumps({'students': payload}),))
        self.fixture.db.commit()
        a, b = self.start(1), self.start(2)
        self.fixture.db.execute("""CREATE TRIGGER deny_any_barred_import_update BEFORE UPDATE ON user_import_job_results
          WHEN (EXISTS(SELECT 1 FROM student_deletion_barriers WHERE student_id=1) AND NEW.rows_data LIKE '%"student_id":1%')
            OR (EXISTS(SELECT 1 FROM student_deletion_barriers WHERE student_id=2) AND NEW.rows_data LIKE '%"student_id":2%')
          BEGIN SELECT RAISE(ABORT,'pending'); END""")
        self.fixture.db.commit()
        self.fixture.finish_homework(a); self.fixture.finish_homework(b)
        for _ in range(8): self.fixture.due(); jobs.run_once()
        self.assertEqual(jobs.get_job(a['id'])['status'], 'completed')
        self.assertEqual(jobs.get_job(b['id'])['status'], 'completed')
        self.assertEqual(json.loads(self.fixture.db.execute('SELECT rows_data FROM user_import_job_results').fetchone()[0]), [])
        self.assertEqual(self.fixture.db.execute('SELECT id FROM students').fetchall(), [(3,)])

    def test_rating_job_error_fragments_are_redacted(self):
        self.fixture.db.execute('CREATE TABLE rating_recalc_jobs(id INTEGER PRIMARY KEY,errors TEXT)')
        self.fixture.db.execute('INSERT INTO rating_recalc_jobs VALUES(1,?)', (json.dumps([{'student_id': 1, 'error': 'Synthetic Target'}, {'student_id': 2, 'error': 'unrelated'}]),))
        self.fixture.db.commit()
        job = self.start(); self.fixture.finish_homework(job)
        for _ in range(4): self.fixture.due(); jobs.run_once()
        self.assertEqual(jobs.get_job(job['id'])['status'], 'completed')
        self.assertEqual(json.loads(self.fixture.db.execute('SELECT errors FROM rating_recalc_jobs').fetchone()[0]), [{'student_id': 2, 'error': 'unrelated'}])

    def test_shared_import_multiple_columns_redacted_in_single_update(self):
        self.fixture.db.execute('ALTER TABLE user_import_jobs ADD COLUMN errors TEXT')
        self.fixture.db.execute("INSERT INTO user_import_jobs(id,session_id,status,entities_created,errors) VALUES(5,8,'completed',?,?)", (json.dumps({'student_ids_created': [2, 3]}), json.dumps([{'student_id': 1, 'error': 'Synthetic Target'}, {'student_id': 3, 'error': 'keep'}])))
        self.fixture.db.commit()
        a, b = self.start(1), self.start(2)
        self.fixture.db.execute("""CREATE TRIGGER deny_any_barred_import_columns BEFORE UPDATE ON user_import_jobs
          WHEN NEW.errors LIKE '%"student_id":1%' OR NEW.entities_created LIKE '%[2%'
          BEGIN SELECT RAISE(ABORT,'pending'); END""")
        self.fixture.db.commit()
        self.fixture.finish_homework(a); self.fixture.finish_homework(b)
        for _ in range(8): self.fixture.due(); jobs.run_once()
        self.assertEqual(jobs.get_job(a['id'])['status'], 'completed')
        self.assertEqual(jobs.get_job(b['id'])['status'], 'completed')
        entities, errors = self.fixture.db.execute('SELECT entities_created,errors FROM user_import_jobs').fetchone()
        self.assertEqual(json.loads(entities), {'student_ids_created': [3]})
        self.assertEqual(json.loads(errors), [{'student_id': 3, 'error': 'keep'}])

    def test_reused_login_never_overrides_explicit_other_owner(self):
        other = {'student_id': 2, 'row': 2, 'login': 'synth1', 'full_name': 'Synthetic Other'}
        self.fixture.db.execute("INSERT INTO user_import_jobs VALUES(5,8,'completed',NULL)")
        self.fixture.db.execute('INSERT INTO user_import_job_results VALUES(5,?)', (json.dumps([other]),))
        self.fixture.db.commit()
        job = self.start(); self.fixture.finish_homework(job)
        for _ in range(4): self.fixture.due(); jobs.run_once()
        self.assertEqual(jobs.get_job(job['id'])['status'], 'completed')
        self.assertEqual(json.loads(self.fixture.db.execute('SELECT rows_data FROM user_import_job_results').fetchone()[0]), [other])

    def test_conflicting_explicit_snapshot_owners_block_before_deletion(self):
        self.fixture.db.execute('INSERT INTO user_import_sessions VALUES(8,?)', (json.dumps([{'student_id': 1, 'studentId': 2}]),)); self.fixture.db.commit()
        self.assertFalse(jobs.preview(1, self.actor)['available'])
        self.assertEqual(self.fixture.db.execute('SELECT COUNT(*) FROM student_deletion_barriers').fetchone()[0], 0)

    def test_login_only_history_is_ambiguous_not_erased(self):
        self.fixture.db.execute('INSERT INTO user_import_sessions VALUES(8,?)', (json.dumps([{'login': 'synth1', 'raw': 'historical ownership unknown'}]),)); self.fixture.db.commit()
        self.assertFalse(jobs.preview(1, self.actor)['available'])

    def test_edited_import_preview_row_number_does_not_delete_replacement(self):
        self.fixture.db.execute("INSERT INTO user_import_jobs VALUES(5,8,'completed',NULL)")
        self.fixture.db.execute('INSERT INTO user_import_job_results VALUES(5,?)', (json.dumps([{'student_id': 1, 'row': 5, 'full_name': 'Synthetic Target'}]),))
        replacement = {'row': 5, 'full_name': 'New Unrelated Person'}
        self.fixture.db.execute('INSERT INTO user_import_sessions VALUES(8,?)', (json.dumps({'students': [replacement]}),))
        self.fixture.db.commit()
        job = self.start(); self.fixture.finish_homework(job)
        for _ in range(4): self.fixture.due(); jobs.run_once()
        self.assertEqual(jobs.get_job(job['id'])['status'], 'completed')
        preview = json.loads(self.fixture.db.execute('SELECT preview_payload FROM user_import_sessions').fetchone()[0])
        self.assertEqual(preview['students'], [replacement])

    def test_unattributed_legacy_snapshot_requires_safe_repair(self):
        self.fixture.db.execute('INSERT INTO user_import_sessions VALUES(8,?)', (json.dumps({'raw': 'Synthetic Target'}),)); self.fixture.db.commit()
        view = jobs.preview(1, self.actor)
        self.assertFalse(view['available']); self.assertIn('сопоставить', view['blockers'][0])


if __name__ == '__main__':
    unittest.main()
