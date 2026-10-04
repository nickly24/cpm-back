"""Synthetic real MySQL8 single-session deletion schema/trigger checks."""
import importlib.util
from pathlib import Path
import json
import os
import ast
import types
import hashlib
from mysql_init_sql import split_sql, run

BACKEND=Path(__file__).resolve().parents[2]
HOMEWORK=Path(os.environ.get('CPM_TEST_HOMEWORK_REPO', str(BACKEND.parent/'cpm-homework-service'))).resolve()
if not (HOMEWORK/'migrations/002_student_purges.sql').is_file():
    raise SystemExit('Set CPM_TEST_HOMEWORK_REPO to the paired homework checkout')
spec=importlib.util.spec_from_file_location('isolated_fence', BACKEND/'cpm_back/services/student_deletion/fence.py')
fence=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fence)  # File imports Flask only, never application/config.

statements=[
    'CREATE DATABASE cpm_synthetic_deletion_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci',
    'USE cpm_synthetic_deletion_test',
    'CREATE TABLE students (id INT PRIMARY KEY, full_name VARCHAR(128), group_id INT) ENGINE=InnoDB',
    'CREATE TABLE auth_users (id BIGINT PRIMARY KEY, role VARCHAR(24), ref_id BIGINT) ENGINE=InnoDB',
    'CREATE TABLE homework (id INT PRIMARY KEY, name VARCHAR(128), deadline DATETIME, published BOOL) ENGINE=InnoDB',
    'CREATE TABLE homework_sessions (id BIGINT PRIMARY KEY, homework_id INT, student_id INT, status VARCHAR(24), result FLOAT, date_pass DATETIME) ENGINE=InnoDB',
    'CREATE TABLE proctors (id INT PRIMARY KEY, group_id INT, full_name VARCHAR(128)) ENGINE=InnoDB',
]
statements+=split_sql(BACKEND/'migrations/030_student_deletion_jobs.sql')
statements+=split_sql(HOMEWORK/'migrations/001_homework_files.sql')
statements+=split_sql(HOMEWORK/'migrations/002_student_purges.sql')
# Repeat additive schema to check compatibility/idempotence before guard install.
statements+=split_sql(BACKEND/'migrations/030_student_deletion_jobs.sql')
statements+=split_sql(HOMEWORK/'migrations/001_homework_files.sql')
statements+=split_sql(HOMEWORK/'migrations/002_student_purges.sql')
created={'students','auth_users','homework_sessions','homework_submissions','homework_file_jobs','homework_submission_files','homework_object_ownership'}
for table, key in fence.DIRECT_TABLES.items():
    if table not in created:
        statements.append(f'CREATE TABLE `{table}` (id BIGINT PRIMARY KEY, `{key}` BIGINT) ENGINE=InnoDB')
        created.add(table)
for table,(key,parent,owner) in fence.INDIRECT_TABLES.items():
    if table not in created:
        statements.append(f'CREATE TABLE `{table}` (id BIGINT PRIMARY KEY, `{key}` BIGINT) ENGINE=InnoDB')
        created.add(table)
statements += [
    'CREATE TABLE classic_exam_votes (id BIGINT PRIMARY KEY, round_id BIGINT) ENGINE=InnoDB',
    'CREATE TABLE exam_admin_commands (id BIGINT PRIMARY KEY, target_student_id BIGINT, target_attempt_id BIGINT, receipt JSON) ENGINE=InnoDB',
    "INSERT INTO students VALUES(101,'Synthetic deleted student',1),(202,'Synthetic retained student',1)",
    "INSERT INTO auth_users VALUES(1,'student',101),(2,'student',202),(3,'proctor',101)",
    'INSERT INTO zaps VALUES(1,101),(2,202)',
    'INSERT INTO zap_dates VALUES(1,1)',
    'INSERT INTO classic_exam_assignments VALUES(1,101),(2,202)',
    'INSERT INTO classic_exam_attempts VALUES(1,101),(2,202)',
    'INSERT INTO classic_exam_vote_rounds VALUES(1,1),(2,2)',
    'INSERT INTO classic_exam_votes VALUES(1,1)',
    'INSERT INTO exam_admin_commands(id,target_student_id,target_attempt_id) VALUES(1,101,1)',
]
for table,fields in fence.JOURNAL_COLUMNS.items():
    if table == 'exam_admin_commands':
        continue
    statements.append('CREATE TABLE `'+table+'` (id BIGINT PRIMARY KEY, '+', '.join('`'+field+'` JSON' for field in fields)+') ENGINE=InnoDB')
statements += [
    "INSERT INTO user_import_sessions VALUES(1,JSON_OBJECT('student_id',101)),(2,JSON_OBJECT('student_id',202))",
    "INSERT INTO user_import_jobs VALUES(1,JSON_ARRAY(JSON_OBJECT('student_id',101)),JSON_OBJECT('student_ids_created',JSON_ARRAY(101,202)),JSON_OBJECT('student',JSON_OBJECT('id',101)))",
]
statements+=fence._migration_statements()
triggers=list(fence._trigger_definitions(set(fence.DIRECT_TABLES)|set(fence.INDIRECT_TABLES)|fence.SPECIAL_TABLES|set(fence.JOURNAL_COLUMNS)))
for name,table,event,timing,body in triggers:
    statements.append(f'CREATE TRIGGER `{name}` {timing} {event} ON `{table}` FOR EACH ROW {body}')
# Assertion routine fails the engine run for any missed/wrong denial.
statements.append("CREATE PROCEDURE assert_true(IN p BOOLEAN, IN label VARCHAR(128)) BEGIN IF NOT p OR p IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT=label; END IF; END")
statements += [
    "INSERT INTO attendance VALUES(1,101),(2,202)",
    "SET SESSION time_zone='+03:00'",
    "INSERT INTO student_deletion_barriers(student_id,job_id,created_at) VALUES(101,'11111111-1111-4111-8111-111111111111',UTC_TIMESTAMP(6))",
    "CALL assert_true(ABS(TIMESTAMPDIFF(SECOND,(SELECT created_at FROM student_deletion_barriers WHERE student_id=101),UTC_TIMESTAMP(6)))<5,'barrier_utc_in_non_utc_session')",
]
checks=[]
def denied(sql, label, expected='student_deletion_pending'):
    name='case_'+str(len(checks)+1)
    statements.append(f"CREATE PROCEDURE {name}() BEGIN DECLARE error_text TEXT DEFAULT NULL; BEGIN DECLARE CONTINUE HANDLER FOR SQLEXCEPTION BEGIN GET DIAGNOSTICS CONDITION 1 error_text=MESSAGE_TEXT; END; {sql}; END; CALL assert_true(error_text='{expected}', '{label}'); END")
    statements.append(f'CALL {name}()')
    checks.append(label)
denied("INSERT INTO attendance VALUES(3,101)",'block_direct_insert')
denied("UPDATE attendance SET student_id=202 WHERE id=1",'block_old_owner_update')
denied("UPDATE attendance SET student_id=101 WHERE id=2",'block_new_owner_update')
denied("UPDATE students SET full_name='Must reject' WHERE id=101",'block_student_update')
denied("INSERT INTO auth_users VALUES(4,'student',101)",'block_student_auth_insert')
denied("UPDATE auth_users SET role='proctor' WHERE id=1",'block_student_auth_role_change')
denied("INSERT INTO zap_dates VALUES(2,1)",'block_indirect_insert')
denied("UPDATE zap_dates SET zap_id=2 WHERE id=1",'block_old_indirect_owner_update')
denied("INSERT INTO zap_dates VALUES(3,999)",'block_missing_indirect_parent','student_write_owner_missing')
denied("INSERT INTO classic_exam_votes VALUES(2,1)",'block_multihop_vote_insert')
denied("UPDATE classic_exam_votes SET round_id=2 WHERE id=1",'block_old_multihop_vote_owner')
denied("INSERT INTO exam_admin_commands(id,target_student_id,target_attempt_id) VALUES(2,NULL,1)",'block_attempt_scoped_command')
denied("UPDATE student_deletion_barriers SET job_id='22222222-2222-4222-8222-222222222222' WHERE student_id=101",'immutable_barrier_update','student_deletion_barrier_immutable')
denied("DELETE FROM student_deletion_barriers WHERE student_id=101",'immutable_barrier_delete','student_deletion_barrier_immutable')
statements += [
    "INSERT INTO attendance VALUES(4,202)",
    "INSERT INTO auth_users VALUES(5,'proctor',101)",
    "UPDATE students SET full_name='Retained user updated' WHERE id=202",
    "CALL assert_true((SELECT COUNT(*) FROM attendance WHERE student_id=101)=1, 'failed_writes_rolled_back')",
    "CALL assert_true((SELECT COUNT(*) FROM attendance WHERE student_id=202)=2, 'retained_student_writes_preserved')",
    "DELETE FROM attendance WHERE student_id=101",
    "DELETE FROM auth_users WHERE role='student' AND ref_id=101",
    "DELETE FROM students WHERE id=101",
]
denied("INSERT INTO students VALUES(101,'Recreated deleted identity',1)",'block_student_id_recreation')
statements += [
    "CALL assert_true((SELECT COUNT(*) FROM students WHERE id=101)=0, 'deleted_profile_absent')",
    "CALL assert_true((SELECT COUNT(*) FROM students WHERE id=202)=1, 'collateral_profile_retained')",
    "CALL assert_true((SELECT COUNT(*) FROM auth_users WHERE role='proctor' AND ref_id=101)=2, 'role_scoped_collision_preserved')",
    "CALL assert_true((SELECT COUNT(*) FROM student_deletion_barriers WHERE student_id=101)=1, 'tombstone_retained')",
]
# JSON guards inspect every supported numeric/string ownership representation.
json_cases=[]
for key in ('student_id','studentId','studentID','existing_student_id','target_student_id'):
    for value in (101,'101'):
        json_cases.append((key+'_'+type(value).__name__, {'rows':[{'nested':{key:value}}]}))
for key in ('student_ids','studentIds','student_ids_created'):
    for value in (101,'101'):
        json_cases.append((key+'_'+type(value).__name__, {'rows':[{key:[202,value]}]}))
for value in (101,'101'):
    json_cases.append(('student_nested_'+type(value).__name__, {'rows':[{'student':{'id':value}}]}))
for index,(label,document) in enumerate(json_cases,10):
    literal='CONVERT(0x'+json.dumps(document).encode().hex()+' USING utf8mb4)'
    denied(f'INSERT INTO user_import_sessions VALUES({index},{literal})','block_json_'+label)
denied("UPDATE user_import_sessions SET preview_payload=JSON_OBJECT('studentId',101) WHERE id=2",'block_json_stale_update')
denied("INSERT INTO exam_admin_commands(id,target_student_id,target_attempt_id,receipt) VALUES(20,NULL,NULL,JSON_OBJECT('student_id',101))",'block_shared_command_json')
denied("INSERT INTO rating_recalc_jobs VALUES(1,JSON_ARRAY(JSON_OBJECT('student_id',101)))",'block_rating_error_json')
statements += [
    "UPDATE user_import_sessions SET preview_payload=JSON_OBJECT('student_id',8) WHERE id=1",
    "CALL assert_true(JSON_EXTRACT((SELECT preview_payload FROM user_import_sessions WHERE id=1),'$.student_id')=8,'allow_new_only_json_redaction')",
    "UPDATE user_import_jobs SET errors=JSON_ARRAY(),entities_created=JSON_OBJECT('student_ids_created',JSON_ARRAY(202)),summary=JSON_OBJECT('student',JSON_OBJECT('id',202)) WHERE id=1",
    "CALL assert_true(JSON_LENGTH((SELECT errors FROM user_import_jobs WHERE id=1))=0,'allow_atomic_multicolumn_redaction')",
]

# Exercise the actual enqueue_delete function without importing application/config.
outbox_path=HOMEWORK/'homework_service/outbox.py'
node=next(n for n in ast.parse(outbox_path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='enqueue_delete')
namespace={'db':types.SimpleNamespace(deletion_protection_active=lambda:True)}
exec(compile(ast.Module(body=[node],type_ignores=[]),str(outbox_path),'exec'),namespace)
class CaptureCursor:
    def execute(self, sql, params):
        self.sql,self.params=sql,params

def quote_sql(value):
    if value is None:
        return 'NULL'
    if isinstance(value,int):
        return str(value)
    return "'"+str(value).replace("\\","\\\\").replace("'","''")+"'"

def interpolate(sql,params):
    parts=sql.split('%s')
    if len(parts)!=len(params)+1:
        raise AssertionError('SQL placeholder mismatch')
    return ''.join(part+quote_sql(value) for part,value in zip(parts,params))+parts[-1]

def enqueue(delay=0):
    cursor=CaptureCursor()
    namespace['enqueue_delete'](cursor,'synthetic/student202/object.pdf',delay)
    return interpolate(cursor.sql,cursor.params)

queue="object_key='synthetic/student202/object.pdf'"
statements += [
    enqueue(1),
    f"CALL assert_true((SELECT generation=1 AND status='queued' AND attempts=0 FROM homework_s3_delete_queue WHERE {queue}),'outbox_first_generation')",
    f"CALL assert_true(ABS(TIMESTAMPDIFF(SECOND,UTC_TIMESTAMP(6),(SELECT available_at FROM homework_s3_delete_queue WHERE {queue}))-3600)<5,'outbox_utc_delay_non_utc_session')",
    f"INSERT INTO homework_s3_delete_receipts(queue_id,generation,object_key,completed_at) SELECT id,generation,object_key,UTC_TIMESTAMP(6) FROM homework_s3_delete_queue WHERE {queue}",
    f"UPDATE homework_s3_delete_queue SET status='completed',attempts=8,error_code='synthetic',completed_at=UTC_TIMESTAMP(6),lease_owner='expired',lease_expires_at=UTC_TIMESTAMP(6) WHERE {queue}",
    enqueue(),
    f"CALL assert_true((SELECT generation=2 AND status='queued' AND attempts=0 AND error_code IS NULL AND completed_at IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL FROM homework_s3_delete_queue WHERE {queue}),'outbox_rearms_terminal_generation')",
    "CALL assert_true((SELECT COUNT(*) FROM homework_s3_delete_receipts WHERE generation=1)=1,'outbox_preserves_previous_receipt')",
    f"UPDATE homework_s3_delete_queue SET status='running',attempts=7,lease_owner='active',lease_expires_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 60 SECOND) WHERE {queue}",
    enqueue(2),
    f"CALL assert_true((SELECT generation=2 AND status='running' AND attempts=7 AND lease_owner='active' FROM homework_s3_delete_queue WHERE {queue}),'outbox_preserves_running_generation_and_lease')",
    f"UPDATE homework_s3_delete_queue SET status='cancelled' WHERE {queue}",
    enqueue(),
    f"CALL assert_true((SELECT generation=3 AND status='queued' FROM homework_s3_delete_queue WHERE {queue}),'outbox_rearms_cancelled_generation')",
    f"UPDATE homework_s3_delete_queue SET status='failed' WHERE {queue}",
    enqueue(),
    f"CALL assert_true((SELECT generation=4 AND status='queued' FROM homework_s3_delete_queue WHERE {queue}),'outbox_rearms_failed_generation')",
    enqueue(),
    f"CALL assert_true((SELECT generation=4 AND status='queued' FROM homework_s3_delete_queue WHERE {queue}),'outbox_queued_duplicate_idempotence')",
]

# Use current coordinator INSERT statement literals, not copied legacy SQL.
jobs_path=BACKEND/'cpm_back/services/student_deletion/jobs.py'
job_ast=ast.parse(jobs_path.read_text())
def insert_template(table):
    for node in ast.walk(job_ast):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='execute' and node.args and isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,str):
            query=node.args[0].value
            if query.lstrip().startswith('INSERT INTO '+table+'(') or query.lstrip().startswith('INSERT INTO '+table+'\n'):
                return query
    raise AssertionError('Coordinator INSERT template missing: '+table)
job_id='22222222-2222-4222-8222-222222222222'
statements += [
    interpolate(insert_template('student_deletion_jobs'),(job_id,202,'admin',99,'33333333-3333-4333-8333-333333333333','a'*64,'b'*64,'{"id":202}','{}',1)),
    interpolate(insert_template('homework_student_purges'),(job_id,202)),
    "CALL assert_true((SELECT ABS(TIMESTAMPDIFF(SECOND,created_at,UTC_TIMESTAMP(6)))<5 AND ABS(TIMESTAMPDIFF(SECOND,updated_at,UTC_TIMESTAMP(6)))<5 AND available_at<=UTC_TIMESTAMP(6) FROM student_deletion_jobs WHERE student_id=202),'job_times_utc_non_utc_session')",
    "CALL assert_true((SELECT ABS(TIMESTAMPDIFF(SECOND,created_at,UTC_TIMESTAMP(6)))<5 AND ABS(TIMESTAMPDIFF(SECOND,updated_at,UTC_TIMESTAMP(6)))<5 AND available_at<=UTC_TIMESTAMP(6) FROM homework_student_purges WHERE student_id=202),'purge_times_utc_non_utc_session')",
]

statements += [
    "SELECT JSON_ARRAYAGG(JSON_OBJECT('TRIGGER_NAME',TRIGGER_NAME,'EVENT_OBJECT_TABLE',EVENT_OBJECT_TABLE,'EVENT_MANIPULATION',EVENT_MANIPULATION,'ACTION_TIMING',ACTION_TIMING,'ACTION_STATEMENT',ACTION_STATEMENT)) INTO DUMPFILE '@@LOCAL_OUTPUT_DIR@@/triggers.json' FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()",
    "SELECT JSON_ARRAYAGG(JSON_OBJECT('ROUTINE_NAME',ROUTINE_NAME,'ROUTINE_DEFINITION',ROUTINE_DEFINITION)) INTO DUMPFILE '@@LOCAL_OUTPUT_DIR@@/routines.json' FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=DATABASE() AND ROUTINE_NAME IN ('cpm_assert_student_write','cpm_assert_student_json_write')",
]
workspace=run(statements,'deletion')
actual_triggers={row['TRIGGER_NAME']:row for row in json.loads((workspace/'out/triggers.json').read_text())}
for name,table,event,timing,body in triggers:
    row=actual_triggers[name]
    assert (row['EVENT_OBJECT_TABLE'],row['EVENT_MANIPULATION'],row['ACTION_TIMING'],fence._normalize_sql(row['ACTION_STATEMENT'])) == (table,event,timing,fence._normalize_sql(body)), name
actual_routines={row['ROUTINE_NAME']:row['ROUTINE_DEFINITION'] for row in json.loads((workspace/'out/routines.json').read_text())}
for expected in fence._migration_statements():
    if fence._normalize_sql(expected).startswith('create procedure cpm_assert_student_'):
        name=expected.split()[2].split('(')[0]
        assert fence._normalize_sql(actual_routines[name])==fence._normalize_sql(expected[expected.upper().index('BEGIN'):]), name
print('Persisted MySQL trigger and routine metadata matches readiness checker')
report={'engine':'MySQL 8.0.46','execution':'socket-free initialization, single session','migrations':['backend030','backend031','homework001','homework002'], 'dynamic_triggers':len(triggers),'metadata_matches_readiness_checker':True,'json_negative_checks':len(json_cases)+3,'outbox_generation_checks':8,'timezone_session':'+03:00','utc_timestamp_checks':4,'negative_checks':checks,'passed':True,'limits':['No client/server protocol','No concurrent sessions or transaction interleaving','Minimal synthetic table shapes beyond exact homework/deletion migrations','No application API or real Mongo/S3 integration']}
report['source_sha256']={str(path.relative_to(BACKEND)) if BACKEND in path.parents else 'homework/'+str(path.relative_to(HOMEWORK)):hashlib.sha256(path.read_bytes()).hexdigest() for path in [BACKEND/'cpm_back/services/student_deletion/fence.py',BACKEND/'migrations/030_student_deletion_jobs.sql',BACKEND/'migrations/031_student_deletion_write_barriers.sql',jobs_path,HOMEWORK/'migrations/001_homework_files.sql',HOMEWORK/'migrations/002_student_purges.sql',outbox_path]}
(workspace/'report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
