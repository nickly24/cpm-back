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
        if hasattr(self.fixture, 'statements'):
            self.fixture.statements.append(sql)
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
        if 'ON DUPLICATE KEY UPDATE' in sql:
            sql = sql.split('ON DUPLICATE KEY UPDATE')[0]+"ON CONFLICT(object_key) DO UPDATE SET status='queued',available_at=excluded.available_at,attempts=0,error_code=NULL"
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
        self.statements = []
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
        CREATE TABLE homework_s3_delete_queue(id INTEGER PRIMARY KEY,object_key TEXT UNIQUE,status TEXT,attempts INTEGER DEFAULT 0,available_at TEXT,error_code TEXT);
        CREATE TABLE student_card_progress(id INTEGER PRIMARY KEY,student_id INTEGER,card_ref TEXT,learned_at TEXT);
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
        INSERT INTO homework_service_capabilities VALUES('student-purge',2,CURRENT_TIMESTAMP);
        ''')
        self.db.execute("INSERT INTO student_card_progress VALUES(1,1,'card-one','2026-01-01'),(2,2,'card-two','2026-01-01')")
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
        # Simulate the existing file worker acknowledging all target exact keys.
        for key in self.mongo[inventory.QUEUE_COLLECTION].find_one({'id': job['id']}).get('file_keys', []):
            self.db.execute('DELETE FROM homework_s3_delete_queue WHERE object_key=?', (key,))
        self.db.commit()

    def due(self):
        self.mongo[inventory.QUEUE_COLLECTION].update_many({}, {'$set': {'available_at': datetime(2000,1,1)}})
