"""Real MySQL engine checks using every table from the supplied audited schema.

Only restricted CREATE TABLE statements, our migrations, and synthetic fixtures
are executed. Never imports application/config, supplied history or procedures.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import importlib.util
import json
import os
import sys

from mysql_init_sql import run, split_sql
BACKEND=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('sql_generator',BACKEND/'scripts/generate_student_deletion_sql.py')
gen=importlib.util.module_from_spec(spec);spec.loader.exec_module(gen)


def metadata_export(name="post_metadata"):
    parts=[]
    for key,(source,schema_field,fields,keys) in gen.SCHEMA.items():
        if key=='routines': fields=('ROUTINE_NAME','ROUTINE_TYPE','ROUTINE_DEFINITION')
        if key=='triggers': fields=('TRIGGER_NAME','EVENT_OBJECT_TABLE','EVENT_MANIPULATION','ACTION_TIMING','ACTION_STATEMENT')
        obj='JSON_OBJECT('+','.join("'"+f+"',`"+f+'`' for f in fields)+')'
        parts += ["'"+key+"'",f"(SELECT COALESCE(JSON_ARRAYAGG({obj}),JSON_ARRAY()) FROM {source} s WHERE `{schema_field}`=DATABASE())"]
    return "SELECT JSON_OBJECT("+','.join(parts)+") INTO OUTFILE '@@LOCAL_OUTPUT_DIR@@/"+name+".json' FIELDS ESCAPED BY ''"


def output_query(query,name):
    return f"SELECT COALESCE(JSON_ARRAYAGG(JSON_OBJECT('check_name',check_name,'mismatch_count',mismatch_count)),JSON_ARRAY()) INTO OUTFILE '@@LOCAL_OUTPUT_DIR@@/{name}.json' FIELDS ESCAPED BY '' FROM ({query}) cpm_result"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit',required=True,type=Path)
    parser.add_argument('--compile-only',action='store_true')
    args=parser.parse_args()
    metadata=json.loads((args.audit/'schema-metadata.json').read_text())
    statements=['CREATE DATABASE cpm_actual_schema_test CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci','USE cpm_actual_schema_test']
    statements+=gen.safe_create_tables(metadata)
    for key in ('tables','columns','foreign_keys','indexes','triggers'):
        for n,query in enumerate(gen.drift_queries(metadata,key)):
            statements.append(output_query(query,'baseline_'+key+'_'+str(n)))
    statements.append(metadata_export('baseline_metadata'))
    # Execute every aggregate read-only statement from the delivered preflight.
    for n,query in enumerate(split_sql(gen.DEST/'000_preflight_read_only.sql')[2:]):
        statements.append(output_query(query,'bundle_preflight_'+str(n)))
    statements+=['SET @cpm_deletion_prepare_confirmed=1']
    statements+=split_sql(gen.DEST/'030_002_prepare.sql')
    # Both additive preparation migrations must support repeated execution.
    statements+=split_sql(gen.DEST/'030_002_prepare.sql')
    preparation_checks=[]
    if not args.compile_only:
        from actual_preparation_sql_cases import preparation_cases
        preparation_sql,preparation_checks=preparation_cases(gen)
        statements+=preparation_sql
    statements+=['SET @cpm_deletion_maintenance_confirmed=1']
    statements+=split_sql(gen.DEST/'031_activate_all_write_guards.sql')
    statements.append(metadata_export())
    if not args.compile_only and (gen.DEST/'032_verify_and_detect_drift.sql').exists():
        for n,query in enumerate(split_sql(gen.DEST/'032_verify_and_detect_drift.sql')):
            statements.append(output_query(query,'bundle_postcheck_'+str(n)))
    statements.append("CREATE PROCEDURE cpm_test_assert(IN p BOOLEAN,IN label VARCHAR(128)) BEGIN IF p IS NULL OR NOT p THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT=label; END IF; END")
    checks=list(preparation_checks)
    def assert_sql(condition,label):
        statements.append(f"CALL cpm_test_assert({condition},'{label}')");checks.append(label)
    def denied(sql,label,expected='student_deletion_pending'):
        proc='cpm_test_case_'+str(len(checks)+1)
        statements.append(f"CREATE PROCEDURE {proc}() BEGIN DECLARE error_text TEXT DEFAULT NULL; BEGIN DECLARE CONTINUE HANDLER FOR SQLEXCEPTION BEGIN GET DIAGNOSTICS CONDITION 1 error_text=MESSAGE_TEXT; END; {sql}; END; CALL cpm_test_assert(error_text={gen.literal(expected)},'{label}'); END")
        statements.append(f'CALL {proc}()');checks.append(label)
    if not args.compile_only:
        from actual_schema_fixtures import add_checks
        add_checks(statements,checks,assert_sql,denied,gen)
        # Negative drift probes run after synthetic deletion; they are restored.
        expected=json.loads((gen.DEST/'expected-post-activation.json').read_text())
        def drift_sum(key,table=None):
            selected=copy.deepcopy(expected)
            if table:selected[key]=[r for r in selected[key] if r['TABLE_NAME']==table]
            query=gen.drift_query(selected,key,[table] if table else None)
            if key=='routines':query=query.replace('WHERE `ROUTINE_SCHEMA`=DATABASE()',"WHERE `ROUTINE_SCHEMA`=DATABASE() AND ROUTINE_NAME NOT LIKE 'cpm_test_%'")
            return '(SELECT SUM(mismatch_count) FROM ('+query+') checked)'
        statements.append('ALTER TABLE students ADD COLUMN synthetic_unexpected_column INT NULL')
        assert_sql(drift_sum('columns','students')+'=1','drift_unexpected_column_detected')
        statements.append('ALTER TABLE students DROP COLUMN synthetic_unexpected_column')
        statements.append('ALTER TABLE attendance DROP FOREIGN KEY attendance_ibfk_1')
        assert_sql(drift_sum('foreign_keys','attendance')+'=1','drift_missing_fk_detected')
        statements.append('ALTER TABLE attendance ADD CONSTRAINT attendance_ibfk_1 FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE')
        role_sql=next(q for q in gen.load_fence()._migration_statements() if q.startswith('CREATE PROCEDURE cpm_assert_student_role_write('))
        statements+=['DROP PROCEDURE cpm_assert_student_role_write',role_sql.replace("'student'","'Student'")]
        # Four audited historical procedures remain intentionally absent, plus
        # one changed definition appears as missing and unexpected.
        assert_sql(drift_sum('routines')+'=6','drift_role_literal_case_detected_in_sql')
        statements+=['DROP PROCEDURE cpm_assert_student_role_write',role_sql]
    workspace=run(statements,'actual_schema')
    for key in ('tables','columns','foreign_keys','indexes','triggers'):
        for path in (workspace/'out').glob('baseline_'+key+'_*.json'):
            result=json.loads(path.read_text())
            if any(row['mismatch_count'] for row in result):
                raise AssertionError('Actual table baseline mismatch: '+str(path)+': '+json.dumps(result))
    for pattern in ('bundle_preflight_*.json','bundle_postcheck_*.json'):
        for path in (workspace/'out').glob(pattern):
            for row in json.loads(path.read_text()):
                # Historical procedures are intentionally never installed.
                expected=4 if row['check_name']=='routines_missing_or_changed' else 0
                if row['mismatch_count']!=expected:
                    raise AssertionError('Delivered SQL check mismatch: '+str(path)+': '+json.dumps(row))
    post=json.loads((workspace/'out/post_metadata.json').read_text())
    fence=gen.load_fence()
    expected_triggers=list(fence._trigger_definitions(set(gen.load_fence().DIRECT_TABLES)|set(fence.INDIRECT_TABLES)|fence.SPECIAL_TABLES|set(fence.JOURNAL_COLUMNS)))
    actual_triggers={r['TRIGGER_NAME']:r for r in post['triggers']}
    present={r['TABLE_NAME'] for r in post['tables']}
    for name,table,event,timing,body in expected_triggers:
        if table not in present:continue
        row=actual_triggers.get(name)
        assert row and (table,event,timing,fence._normalize_sql(body))==(row['EVENT_OBJECT_TABLE'],row['EVENT_MANIPULATION'],row['ACTION_TIMING'],fence._normalize_sql(row['ACTION_STATEMENT'])),name
    routines={r['ROUTINE_NAME']:r for r in post['routines']}
    for sql in fence._migration_statements():
        if sql.lower().startswith('create procedure cpm_assert_student_'):
            name=sql.split()[2].split('(')[0]
            assert fence._normalize_sql(routines[name]['ROUTINE_DEFINITION'])==fence._normalize_sql(sql[sql.upper().index('BEGIN'):]),name
    class MetadataCursor:
        def execute(self,query,params=()):
            if 'schema_version FROM student_deletion_guard_state' in query:self.rows=[{'schema_version':1}]
            elif 'information_schema.COLUMNS' in query:self.rows=post['columns']
            elif 'information_schema.KEY_COLUMN_USAGE' in query:self.rows=post['foreign_keys']
            elif 'information_schema.TABLES' in query:self.rows=post['tables']
            elif 'information_schema.TRIGGERS' in query:self.rows=post['triggers']
            elif 'information_schema.ROUTINES' in query:self.rows=[r for r in post['routines'] if r['ROUTINE_NAME']==params[0]]
            else:raise AssertionError('Unexpected metadata read '+query)
        def fetchall(self):return self.rows
        def fetchone(self):return self.rows[0] if self.rows else None
        def close(self):pass
    class MetadataConnection:
        def cursor(self,**kwargs):return MetadataCursor()
    fence.verify_write_barrier_schema(MetadataConnection())
    # A literal-case drift in a real persisted guard must fail readiness.
    role_routine=next(r for r in post['routines'] if r['ROUTINE_NAME']=='cpm_assert_student_role_write')
    old_body=role_routine['ROUTINE_DEFINITION']
    role_routine['ROUTINE_DEFINITION']=old_body.replace("'student'","'Student'")
    try:
        fence.verify_write_barrier_schema(MetadataConnection())
    except fence.StudentFenceUnavailable:pass
    else:raise AssertionError('Readiness accepted role literal case drift')
    role_routine['ROUTINE_DEFINITION']=old_body
    tested_paths=[Path(__file__),Path(__file__).with_name('mysql_init_sql.py'),Path(__file__).with_name('actual_schema_fixtures.py'),Path(__file__).with_name('actual_purge_sql_cases.py'),Path(__file__).with_name('actual_preparation_sql_cases.py'),gen.BACKEND.parent/'cpm-homework-service/homework_service/purges.py',gen.BACKEND.parent/'cpm-homework-service/homework_service/file_consistency.py',gen.BACKEND.parent/'cpm-homework-service/homework_service/workflow.py',gen.BACKEND.parent/'cpm-homework-service/homework_service/jobs.py',gen.BACKEND.parent/'cpm-homework-service/homework_service/outbox.py']
    harness_hashes={str(p.relative_to(gen.BACKEND)) if p.is_relative_to(gen.BACKEND) else '../cpm-homework-service/'+str(p.relative_to(gen.BACKEND.parent/'cpm-homework-service')):gen.digest(p) for p in tested_paths}
    report={'passed':True,'harness_and_purge_source_sha256':harness_hashes,'runtime':json.loads((workspace/'runtime.json').read_text()),'delivered_sql_checks_passed':True,'historical_procedure_check_expected_missing':4,'app_readiness_from_real_engine_metadata':True,'literal_case_drift_rejected':True,'initial_tables':len(metadata['tables']),'initial_columns':len(metadata['columns']),'post_tables':len(post['tables']),'post_columns':len(post['columns']),'post_triggers':len(post['triggers']),'post_procedures':len(post['routines']),'statements_executed':len(statements),'synthetic_assertions':checks,'source_sha256':json.loads((gen.DEST/'manifest.json').read_text())['source_sha256'],'not_run':['Exact reported 8.0.22-13 vendor build','Concurrent multi-client locking and crash recovery','HTTP routes with live connector','MongoDB service','S3/provider calls','Existing historical procedures (intentionally never installed or executed)']}
    (workspace/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2));print('POST_METADATA='+str(workspace/'out/post_metadata.json'))

if __name__=='__main__':main()
