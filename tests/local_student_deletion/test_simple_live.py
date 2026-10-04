"""Opt-in local MySQL/Mongo/S3 HTTP tests on the unchanged 79-table schema."""
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import mysql.connector
from pymongo import MongoClient
from flask import Flask, jsonify, g

from cpm_back.services.student_deletion import jobs, inventory
from cpm_back.blueprints.student_deletion_bp import student_deletion_bp
from cpm_back.auth.jwt_auth import generate_token, require_role

HERE=Path(__file__).resolve().parent
LIVE=os.environ.get('CPM_SIMPLE_DELETION_LIVE')=='1'


@unittest.skipUnless(LIVE,'Opt in to disposable loopback databases and local S3 HTTP')
class SimpleDeletionLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.socket_path=os.environ['EXAM_MIGRATION_TEST_SOCKET']
        assert Path(cls.socket_path).resolve().is_relative_to(Path(os.environ['CPM_LOCAL_TEST_RUNTIME']).resolve())
        cls.mysql_options={'unix_socket':cls.socket_path,'user':'root','password':'','autocommit':False}
        cls.mongo_client=MongoClient('mongodb://127.0.0.1:27077',serverSelectionTimeoutMS=3000)
        cls.mongo_client.admin.command('ping')
        sys.path.insert(0,str(HERE.parents[2]/'cpm-homework-service'))
        from homework_service import jobs as file_jobs, db as file_db
        from homework_service.storage import HomeworkStorage
        from moto.server import ThreadedMotoServer
        import boto3
        cls.file_jobs,cls.file_db=file_jobs,file_db
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]
        cls.s3_server=ThreadedMotoServer(ip_address='127.0.0.1',port=port,verbose=False)
        cls.s3_server.start()
        cls.s3=boto3.client('s3',endpoint_url=f'http://127.0.0.1:{port}',region_name='us-east-1',
                           aws_access_key_id='synthetic',aws_secret_access_key='synthetic')
        cls.s3.create_bucket(Bucket='synthetic-student-files')
        cls.storage=HomeworkStorage({'S3_BUCKET':'synthetic-student-files', 'S3_ACCESS_KEY_ID':'synthetic',
                                    'S3_SECRET_ACCESS_KEY':'synthetic'},client=cls.s3)

    @classmethod
    def tearDownClass(cls):
        cls.s3_server.stop();cls.mongo_client.close()

    def setUp(self):
        self.name='cpm_simple_synthetic_'+uuid.uuid4().hex[:12]
        conn=mysql.connector.connect(**self.mysql_options);cursor=conn.cursor()
        cursor.execute('CREATE DATABASE '+self.name+' CHARACTER SET utf8mb4');cursor.close();conn.close()
        self.connection=lambda:mysql.connector.connect(database=self.name,**self.mysql_options)
        conn=self.connection();cursor=conn.cursor()
        ddl=(HERE/'audited-schema.sql').read_text()
        tables={re.search(r'CREATE TABLE `([^`]+)`',sql).group(1):sql for sql in re.findall(r'CREATE TABLE `[^`]+`.*?;',ddl,re.S)}
        remaining=set(tables);created=set()
        while remaining:
            ready=sorted(name for name in remaining if set(re.findall(r'REFERENCES `([^`]+)`',tables[name]))-{name} <= created)
            self.assertTrue(ready,'Cyclic fixture DDL')
            for name in ready:cursor.execute(tables[name]);created.add(name);remaining.remove(name)
        for sql in json.loads((HERE/'synthetic-seed.json').read_text()):cursor.execute(sql)
        conn.commit();cursor.close();conn.close()
        self.mongo=self.mongo_client[self.name]
        for sid in (101,202):
            self.mongo.test_attempts.insert_one({'_id':f'attempt-{sid}','studentId':str(sid),'testId':'shared-test'})
            self.mongo.test_sessions.insert_one({'_id':f'result-{sid}','studentId':sid,'testId':'shared-test','score':80})
            self.mongo.rate_rec.insert_one({'_id':f'rating-{sid}','student_id':sid})
        self.mongo.tests.insert_one({'_id':'shared-test','title':'Synthetic shared definition'})
        self.app=Flask(__name__);self.app.config.update(TESTING=True,JWT_SECRET_KEY='synthetic-unit-secret')
        self.app.register_blueprint(student_deletion_bp)
        self.app.extensions['homework_storage']=self.storage
        @self.app.get('/synthetic/student/check')
        @require_role('student')
        def student_check(current_user):return jsonify(id=current_user['id'])
        context=self.app.app_context();context.push();self.addCleanup(context.pop)
        self.actor={'role':'admin','id':90}
        for item in [patch('cpm_back.services.student_deletion.common.get_db_connection',self.connection),
                     patch('cpm_back.services.student_deletion.common.close_db_connection',lambda c:c.close()),
                     patch.object(jobs,'get_db_connection',self.connection),patch.object(jobs,'close_db_connection',lambda c:c.close()),
                     patch.object(jobs,'get_mongo_db',lambda:self.mongo),patch.object(self.file_db,'connection',self.connection),
                     patch('cpm_back.blueprints.student_deletion_bp.get_current_user',lambda:self.actor)]:
            item.start();self.addCleanup(item.stop)
        self.client=self.app.test_client()
        for key in ('synthetic/one.pdf','synthetic/two.pdf','synthetic/stage-one.pdf','synthetic/stage-two.pdf'):
            self.s3.put_object(Bucket=self.storage.bucket,Key=key,Body=b'%PDF-1.4 synthetic fixture')
        self.addCleanup(self.cleanup)

    def cleanup(self):
        assert self.name.startswith('cpm_simple_synthetic_')
        conn=mysql.connector.connect(**self.mysql_options);cursor=conn.cursor()
        cursor.execute('DROP DATABASE '+self.name);cursor.close();conn.close()
        self.mongo_client.drop_database(self.name)

    def sql(self, query, params=(), mutate=False):
        conn=self.connection();cursor=conn.cursor(dictionary=True,buffered=True)
        try:
            cursor.execute(query,params)
            result=cursor.fetchall() if cursor.with_rows else []
            if mutate:conn.commit()
            return result
        finally:cursor.close();conn.close()

    def start(self):
        preview=self.client.get('/api/students/101/deletion-preview')
        self.assertEqual(preview.status_code,200,preview.json)
        self.assertTrue(preview.json['available'],preview.json)
        response=self.client.post('/api/students/101/deletion-jobs',json={
            'confirmation_token':preview.json['confirmation_token'],'idempotency_key':str(uuid.uuid4())})
        self.assertEqual(response.status_code,202,response.json)
        return response.json['job']

    def files(self):
        for row in self.sql('SELECT * FROM homework_s3_delete_queue'):
            self.file_jobs._delete_object(self.app,row)

    def finish(self, job, process_files=True):
        for _ in range(10):
            self.mongo[inventory.QUEUE_COLLECTION].update_one({'id':job['id']},{'$set':{'available_at':datetime(2000,1,1)}})
            jobs.run_once()
            if process_files:self.files()
            result=jobs.get_job(job['id'])
            if result['status'] in {'completed','partial','failed'}:return result
        return result

    def test_complete_pipeline_on_unchanged_schema_preserves_other_owners(self):
        before=self.sql('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()')
        self.assertEqual(len(before),79)
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()')[0]['n'],0)
        job=self.start()
        self.assertEqual(job['status'],'queued')
        self.assertEqual(len(self.sql('SELECT id FROM students WHERE id=101')),1)
        result=self.finish(job)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['percent'],100)
        self.assertEqual(self.sql('SELECT id FROM students WHERE id=101'),[])
        self.assertEqual(len(self.sql('SELECT id FROM students WHERE id=202')),1)
        self.assertEqual(len(self.sql("SELECT id FROM auth_users WHERE ref_id=101 AND role='proctor'")),1)
        self.assertEqual(len(self.sql('SELECT id FROM homework WHERE id=1')),1)
        self.assertEqual(len(self.sql('SELECT id FROM homework_chat_threads WHERE student_id=202')),1)
        self.assertEqual(self.mongo.test_attempts.count_documents({'studentId':'202'}),1)
        self.assertEqual(self.mongo.tests.count_documents({}),1)
        self.assertTrue(self.storage.verify_absent('synthetic/one.pdf'))
        self.storage.head('synthetic/two.pdf')
        self.assertEqual(self.sql('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()'),before)
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()')[0]['n'],0)

    def test_sql_rollback_leaves_entire_account_and_mongo_intact(self):
        job=self.start()
        def fail(cursor,manifest):
            cursor.execute('DELETE FROM attendance WHERE student_id=%s',(101,))
            raise RuntimeError('synthetic SQL failure')
        with patch.object(jobs,'delete_rows',fail):jobs.run_once()
        result=jobs.get_job(job['id']);self.assertEqual(result['status'],'failed')
        self.assertEqual(len(self.sql('SELECT id FROM students WHERE id=101')),1)
        self.assertEqual(self.mongo.test_attempts.count_documents({'studentId':'101'}),1)
        self.storage.head('synthetic/one.pdf')
        jobs.retry(job['id']);self.assertEqual(self.finish(job)['status'],'completed')

    def test_s3_http_failure_is_reported_and_can_be_retried(self):
        job=self.start();jobs.run_once();jobs.run_once()
        original=self.storage.delete
        with patch.object(self.storage,'delete',side_effect=RuntimeError('synthetic forbidden')):
            self.files()
        self.mongo[inventory.QUEUE_COLLECTION].update_one({'id':job['id']},{'$set':{'file_wait_started_at':datetime(2000,1,1)}})
        result=self.finish(job,False)
        self.assertEqual(result['status'],'partial',result)
        self.assertTrue(any(r['target']=='synthetic/one.pdf' for r in result['residuals']))
        jobs.retry(job['id']);self.assertEqual(self.finish(job)['status'],'completed')
        self.assertTrue(self.storage.verify_absent('synthetic/one.pdf'))
        self.storage.head('synthetic/two.pdf')

    def test_queue_recovers_process_death_after_sql_commit(self):
        job=self.start();original=jobs.checkpoint
        def crash(row,**changes):
            if changes.get('student_deleted'):raise jobs.DeletionError('lease_lost')
            return original(row,**changes)
        with patch.object(jobs,'checkpoint',crash):jobs.run_once()
        self.assertEqual(self.sql('SELECT id FROM students WHERE id=101'),[])
        self.mongo[inventory.QUEUE_COLLECTION].update_one({'id':job['id']},{'$set':{'lease_expires_at':datetime(2000,1,1)}})
        self.assertEqual(self.finish(job)['status'],'completed')

    def test_old_student_token_is_rejected_after_the_sql_stage(self):
        token=generate_token({'role':'student','id':101})
        headers={'Authorization':'Bearer '+token}
        self.assertEqual(self.client.get('/synthetic/student/check',headers=headers).status_code,200)
        job=self.start();jobs.run_once()
        # The test holds an outer app context; reset its request-local auth cache.
        g.pop('_current_user',None)
        self.assertEqual(self.client.get('/synthetic/student/check',headers=headers).status_code,401)
        self.assertTrue(jobs.get_job(job['id'])['student_deleted'])

    def test_file_key_reassigned_to_live_student_is_protected(self):
        job=self.start();jobs.run_once();jobs.run_once()
        self.sql("UPDATE homework_submission_files SET object_key='synthetic/one.pdf' WHERE id=22",mutate=True)
        self.files()
        self.storage.head('synthetic/one.pdf')
        self.assertEqual(self.sql("SELECT status FROM homework_s3_delete_queue WHERE object_key='synthetic/one.pdf'")[0]['status'],'retry')

    def test_background_worker_exposes_progress_while_waiting_for_files(self):
        job = self.start()
        with patch.object(jobs, '_started', False):
            jobs.start_worker(self.app)
            stop = self.app.extensions['student_deletion_worker_stop']
            thread = self.app.extensions['student_deletion_worker_thread']
            try:
                deadline = time.monotonic()+10
                while time.monotonic() < deadline:
                    result = self.client.get('/api/student-deletion-jobs/'+job['id']).json['job']
                    if result['status'] == 'waiting':
                        break
                    time.sleep(0.05)
                self.assertEqual(result['status'], 'waiting', result)
                self.assertTrue(result['student_deleted'])
                self.assertGreater(result['percent'], 0)
                self.assertLess(result['percent'], 100)
                self.assertEqual(self.sql('SELECT id FROM students WHERE id=101'), [])
                self.assertEqual(self.mongo.test_attempts.count_documents({'studentId':'101'}), 0)
                self.files()
                deadline = time.monotonic()+10
                while time.monotonic() < deadline:
                    result = self.client.get('/api/student-deletion-jobs/'+job['id']).json['job']
                    if result['status'] == 'completed':
                        break
                    time.sleep(0.05)
                self.assertEqual(result['status'], 'completed', result)
                self.assertEqual(result['percent'], 100)
            finally:
                stop.set()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

    def test_case_insensitive_existing_file_queue_does_not_acknowledge_another_s3_key(self):
        self.sql("INSERT INTO homework_s3_delete_queue(object_key) VALUES('SYNTHETIC/one.pdf')", mutate=True)
        self.s3.put_object(Bucket=self.storage.bucket, Key='SYNTHETIC/one.pdf', Body=b'synthetic old queued object')
        job = self.start()
        result = self.finish(job)
        self.assertEqual(result['status'], 'partial', result)
        self.assertTrue(any(issue['target']=='synthetic/one.pdf' for issue in result['residuals']))
        self.storage.head('synthetic/one.pdf')
        self.assertTrue(self.storage.verify_absent('SYNTHETIC/one.pdf'))
        jobs.retry(job['id'])
        self.assertEqual(self.finish(job)['status'], 'completed')
        self.assertTrue(self.storage.verify_absent('synthetic/one.pdf'))

    def test_nontransactional_personal_table_blocks_before_any_deletion(self):
        self.sql('CREATE TABLE synthetic_personal_archive(id INT PRIMARY KEY,student_id INT) ENGINE=MyISAM', mutate=True)
        self.sql('INSERT INTO synthetic_personal_archive VALUES(1,101)', mutate=True)
        preview = self.client.get('/api/students/101/deletion-preview').json
        self.assertFalse(preview['available'], preview)
        self.assertTrue(any('synthetic_personal_archive' in message for message in preview['blockers']))
        self.assertEqual(len(self.sql('SELECT id FROM students WHERE id=101')), 1)
        self.assertEqual(self.mongo.test_attempts.count_documents({'studentId':'101'}), 1)
        self.assertEqual(self.mongo[inventory.QUEUE_COLLECTION].count_documents({}), 0)
