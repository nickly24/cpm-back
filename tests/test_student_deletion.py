"""Offline queue, single-transaction and partial-cleanup regressions."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import patch
import uuid

from flask import Flask
from tests.student_deletion_fixtures import Fixture, Cursor, Connection
from cpm_back.blueprints.student_deletion_bp import student_deletion_bp
from cpm_back.services.student_deletion import jobs, inventory, account
from cpm_back.services.student_deletion.common import DeletionError


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, JWT_SECRET_KEY='synthetic-unit-secret')
        self.app.register_blueprint(student_deletion_bp)
        context = self.app.app_context(); context.push()
        self.addCleanup(context.pop)
        self.addCleanup(self.fixture.db.close)
        self.actor = {'role': 'admin', 'id': 90}
        for value in [patch('cpm_back.services.student_deletion.common.get_db_connection', self.fixture.connection),
                      patch('cpm_back.services.student_deletion.common.close_db_connection', lambda c: None),
                      patch.object(jobs, 'get_db_connection', self.fixture.connection),
                      patch.object(jobs, 'close_db_connection', lambda c: None),
                      patch.object(jobs, 'get_mongo_db', lambda: self.fixture.mongo),
                      patch('cpm_back.blueprints.student_deletion_bp.get_current_user', lambda: self.actor),
                      patch('cpm_back.blueprints.student_deletion_bp.has_permission', lambda actor, area, mode: actor['role']=='admin')]:
            value.start(); self.addCleanup(value.stop)
        self.client = self.app.test_client()

    def start(self, student_id=1):
        preview = jobs.preview(student_id, self.actor)
        self.assertTrue(preview['available'], preview['blockers'])
        return jobs.start(student_id, self.actor, preview['confirmation_token'], str(uuid.uuid4()))

    def finish(self, job):
        for _ in range(8):
            self.fixture.due(); jobs.run_once()
            self.fixture.finish_homework(job)
            if jobs.get_job(job['id'])['status'] in {'completed','partial','failed'}:
                break
        return jobs.get_job(job['id'])

    def test_preview_counts_cards_attendance_tests_and_files_without_migrations(self):
        for table in ['student_deletion_jobs','student_deletion_barriers','homework_student_purges',
                      'homework_student_purge_objects','homework_service_capabilities','homework_object_ownership']:
            self.fixture.db.execute('DROP TABLE '+table)
        self.fixture.db.commit()
        preview = jobs.preview(1, self.actor)
        self.assertTrue(preview['available'], preview['blockers'])
        self.assertEqual(preview['counts']['learned_cards'], 1)
        self.assertEqual(preview['counts']['attendance'], 1)
        self.assertEqual(preview['counts']['tests'], 2)
        self.assertEqual(preview['counts']['homework_files'], 2)
        self.assertGreater(preview['counts']['total_records'], 10)
        result = self.finish(self.start())
        self.assertEqual(result['status'], 'completed', result)
        self.assertFalse(any(sql.lstrip().upper().startswith(('CREATE','ALTER','DROP')) for sql in self.fixture.statements))
        self.assertEqual(self.fixture.db.execute('SELECT COUNT(*) FROM sqlite_master WHERE type=\'trigger\'').fetchone()[0], 0)

    def test_queue_returns_before_deleting_and_is_shared_between_requests(self):
        view = self.client.get('/api/students/1/deletion-preview').json
        response = self.client.post('/api/students/1/deletion-jobs', json={'confirmation_token': view['confirmation_token'], 'idempotency_key': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 202)
        job = response.json['job']
        self.assertEqual(job['status'], 'queued')
        self.assertIsNotNone(self.fixture.db.execute('SELECT id FROM students WHERE id=1').fetchone())
        self.assertEqual(self.client.get('/api/student-deletion-jobs/'+job['id']).json['job']['id'], job['id'])
        self.assertEqual(jobs.preview(1,self.actor)['active_job']['id'],job['id'])

    def test_entire_pipeline_preserves_other_student_staff_and_shared_definitions(self):
        self.fixture.db.execute("INSERT INTO auth_users VALUES(4,'admin',1,'staff','synthetic')")
        self.fixture.db.commit()
        result = self.finish(self.start())
        self.assertEqual(result['status'], 'completed', result)
        self.assertTrue(result['student_deleted'])
        self.assertEqual(result['percent'], 100)
        self.assertIsNone(account.load_live_student(1))
        self.assertIsNotNone(account.load_live_student(2))
        for table in ['homework','exams','tests_out']:
            self.assertEqual(self.fixture.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0], 1)
        self.assertEqual(self.fixture.db.execute("SELECT COUNT(*) FROM auth_users WHERE role='admin' AND ref_id=1").fetchone()[0],1)
        for table in inventory.MONGO:
            self.assertEqual(self.fixture.mongo[table].count_documents({}),1)
        self.assertEqual(self.fixture.mongo.tests.count_documents({}),1)

    def test_empty_student_completes(self):
        self.assertEqual(self.finish(self.start(3))['status'],'completed')

    def test_sql_failure_rolls_back_every_table_and_leaves_external_stores_untouched(self):
        job = self.start()
        original = jobs.delete_rows
        def broken(cursor, manifest):
            cursor.execute('DELETE FROM attendance WHERE student_id=%s',(1,))
            raise RuntimeError('synthetic database failure')
        with patch.object(jobs,'delete_rows',broken):
            jobs.run_once()
        result = jobs.get_job(job['id'])
        self.assertEqual(result['status'],'failed')
        self.assertFalse(result['student_deleted'])
        self.assertEqual(self.fixture.db.execute('SELECT COUNT(*) FROM attendance WHERE student_id=1').fetchone()[0],1)
        self.assertIsNotNone(account.load_live_student(1))
        self.assertEqual(self.fixture.mongo.test_attempts.count_documents({'studentId':'1'}),1)
        self.assertEqual(self.fixture.db.execute('SELECT COUNT(*) FROM homework_s3_delete_queue').fetchone()[0],0)
        jobs.retry(job['id'])
        self.assertEqual(self.finish(job)['status'],'completed')

    def test_mongo_error_does_not_prevent_file_cleanup_and_reports_collection(self):
        job = self.start(); jobs.run_once()
        collection = self.fixture.mongo.test_sessions
        with patch.object(collection, 'delete_many', side_effect=RuntimeError('synthetic mongo outage')):
            jobs.run_once()
        result = self.finish(job)
        self.assertEqual(result['status'],'partial')
        self.assertTrue(result['student_deleted'])
        self.assertEqual(result['residuals'][0]['target'],'test_sessions')
        self.assertLess(result['percent'],100)
        jobs.retry(job['id'])
        self.assertEqual(self.finish(job)['status'],'completed')

    def test_file_failures_are_reported_by_exact_key_and_retry_requeues(self):
        job = self.start(); jobs.run_once(); jobs.run_once()
        self.fixture.db.execute("UPDATE homework_s3_delete_queue SET status='failed',error_code='synthetic' WHERE object_key='processed/one'")
        self.fixture.db.commit()
        self.fixture.db.execute("DELETE FROM homework_s3_delete_queue WHERE object_key='staging/one'"); self.fixture.db.commit()
        jobs.run_once(); jobs.run_once()
        result = jobs.get_job(job['id'])
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['residuals'][0]['target'],'processed/one')
        jobs.retry(job['id'])
        self.assertEqual(self.fixture.db.execute("SELECT status FROM homework_s3_delete_queue WHERE object_key='processed/one'").fetchone()[0],'queued')
        self.assertEqual(self.finish(job)['status'],'completed')

    def test_expired_lease_after_sql_commit_recovers_without_losing_file_keys(self):
        job = self.start()
        original = jobs.checkpoint
        def crash(row, **changes):
            if changes.get('student_deleted'):
                raise DeletionError('lease_lost')
            return original(row,**changes)
        with patch.object(jobs,'checkpoint',crash):
            jobs.run_once()
        self.assertIsNone(account.load_live_student(1))
        queue = self.fixture.mongo[inventory.QUEUE_COLLECTION]
        queue.update_one({'id':job['id']},{'$set':{'lease_expires_at':datetime(2000,1,1)}})
        result = self.finish(job)
        self.assertEqual(result['status'],'completed',result)

    def test_network_retry_is_idempotent_and_conflicting_confirmation_is_rejected(self):
        view = jobs.preview(1,self.actor); key=str(uuid.uuid4())
        a=jobs.start(1,self.actor,view['confirmation_token'],key)
        b=jobs.start(1,self.actor,view['confirmation_token'],key)
        self.assertEqual(a['id'],b['id'])
        self.assertEqual(self.fixture.mongo[inventory.QUEUE_COLLECTION].count_documents({}),1)
        with self.assertRaises(DeletionError):
            jobs.start(1,self.actor,'wrong-confirmation',key)

    def test_changed_preview_and_foreign_actor_do_not_enqueue(self):
        view=jobs.preview(1,self.actor)
        self.fixture.db.execute("INSERT INTO attendance VALUES(5,1,'absent')"); self.fixture.db.commit()
        with self.assertRaises(DeletionError) as error:
            jobs.start(1,self.actor,view['confirmation_token'],str(uuid.uuid4()))
        self.assertEqual(error.exception.code,'preview_changed')
        with self.assertRaises(DeletionError):
            jobs.start(1,{'role':'admin','id':91},view['confirmation_token'],str(uuid.uuid4()))
        self.assertEqual(self.fixture.mongo[inventory.QUEUE_COLLECTION].count_documents({}),0)

    def test_auth_and_users_edit_are_required_for_every_endpoint(self):
        for actor,code in [(None,401),({'role':'student','id':1},403)]:
            self.actor=actor
            for path in ['/api/students/1/deletion-preview','/api/students/1/deletion-jobs','/api/student-deletion-jobs/'+str(uuid.uuid4())]:
                self.assertEqual(self.client.get(path).status_code,code)

    def test_unknown_mongo_layout_is_visible_as_manual_cleanup_warning(self):
        self.fixture.mongo.unknown_collection.insert_one({'student_id':1,'value':'synthetic'})
        result=self.finish(self.start())
        self.assertEqual(result['status'],'partial')
        self.assertTrue(any(w['target']=='unknown_collection' for w in result['warnings']))
        self.assertEqual(self.fixture.mongo.unknown_collection.count_documents({}),1)

    def test_shared_file_key_blocks_before_deleting_anything(self):
        self.fixture.db.execute("UPDATE homework_submission_files SET object_key='processed/one' WHERE id=2")
        self.fixture.db.commit()
        view=jobs.preview(1,self.actor)
        self.assertFalse(view['available'])
        self.assertIsNotNone(account.load_live_student(1))

    def test_unknown_direct_student_table_is_included_without_a_migration(self):
        self.fixture.db.execute('CREATE TABLE synthetic_student_note(id INTEGER PRIMARY KEY,student_id INTEGER,note TEXT)')
        self.fixture.db.execute("INSERT INTO synthetic_student_note VALUES(1,1,'one'),(2,2,'two')");self.fixture.db.commit()
        result=self.finish(self.start())
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(self.fixture.db.execute('SELECT id FROM synthetic_student_note').fetchall(),[(2,)])

    def test_final_check_reports_rows_created_after_the_sql_commit(self):
        job = self.start()
        jobs.run_once()
        self.fixture.db.execute("INSERT INTO attendance VALUES(99,1,'absent')")
        self.fixture.db.commit()
        result = self.finish(job)
        self.assertEqual(result['status'], 'partial', result)
        self.assertTrue(result['student_deleted'])
        self.assertTrue(any(issue['source']=='mysql' and issue['target']=='attendance' and issue['count']==1
                            for issue in result['residuals']))

    def test_mongo_document_transferred_between_read_and_delete_survives(self):
        job = self.start()
        jobs.run_once()
        original = inventory.collect_mongo_documents
        transferred = []
        def collect_then_transfer(db, table, student_id):
            result = original(db, table, student_id)
            if table == 'test_attempts' and result[0] and not transferred:
                transferred.append(result[0][0]['_id'])
                db[table].update_one({'_id':transferred[0]}, {'$set':{'studentId':'2'}})
            return result
        with patch.object(jobs, 'collect_mongo_documents', collect_then_transfer):
            result = self.finish(job)
        self.assertEqual(result['status'], 'completed', result)
        self.assertIsNotNone(self.fixture.mongo.test_attempts.find_one({'_id':transferred[0], 'studentId':'2'}))

    def test_student_login_reuses_its_connection_for_the_account_check(self):
        from cpm_back.auth.auth import auth
        with patch('cpm_back.auth.auth.get_db_connection', self.fixture.connection), \
             patch('cpm_back.auth.auth.close_db_connection', lambda connection: None), \
             patch('cpm_back.services.student_deletion.common.get_db_connection', side_effect=AssertionError('extra pool connection')):
            result = auth('synth1', 'synthetic')
        self.assertTrue(result['status'])
        self.assertEqual(result['res']['role'], 'student')
        self.assertEqual(result['res']['group_id'], 1)

    def test_late_shared_import_json_and_exam_receipt_are_reported(self):
        import json
        job = self.start()
        jobs.run_once()
        self.fixture.db.execute('CREATE TABLE classic_exam_assignment_import_sessions(id INTEGER PRIMARY KEY,preview_payload TEXT,commit_result TEXT)')
        payload = json.dumps([{'studentId':1}, {'studentId':2}])
        self.fixture.db.execute('INSERT INTO classic_exam_assignment_import_sessions VALUES(1,?,NULL)', (payload,))
        self.fixture.db.execute("INSERT INTO exam_admin_commands(id,idempotency_key,command_type,target_student_id,receipt) VALUES(99,?,'PUT:exams_v2.student_privilege',1,?)",
                                (str(uuid.uuid4()), json.dumps({'studentId':1})))
        self.fixture.db.commit()
        result = self.finish(job)
        self.assertEqual(result['status'], 'partial', result)
        for table in ('classic_exam_assignment_import_sessions', 'exam_admin_commands'):
            self.assertTrue(any(issue['target']==table and issue['count']==1 for issue in result['residuals']))
        self.assertEqual(self.fixture.db.execute('SELECT preview_payload FROM classic_exam_assignment_import_sessions').fetchone()[0], payload)
