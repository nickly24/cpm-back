#!/usr/bin/env python3
"""Rebuild literal, operator-runnable SQL; never connects to a database.

Inputs are a reviewed metadata-only schema audit and checked-in migration/fence
sources. The delivered .sql files need only the mysql client, not this generator.
"""
from __future__ import annotations
import argparse
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re

BACKEND = Path(__file__).resolve().parents[1]
DEST = BACKEND / 'migrations/student-deletion-schema-audit-v2'
HOMEWORK = BACKEND.parent / 'cpm-homework-service'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def literal(value):
    if value is None:
        return 'NULL'
    if str(value)=='':
        return "''"
    return "_utf8mb4 X'" + str(value).encode('utf-8').hex() + "'"


def normalize(value):
    # Exact persisted definition, preserving SQL literal case and spacing.
    return str(value).replace('\r\n','\n').strip()


def compact_sql(value):
    # Keep literal/identifier bytes, compact only outside quoted SQL tokens.
    pieces = re.split(r"('(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"|`(?:``|[^`])*`)",value)
    return ''.join(piece if n % 2 else re.sub(r'\s+',' ',piece) for n,piece in enumerate(pieces)).strip()


def split_sql(path):
    statements, lines, delimiter = [], [], ';'
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('--'):
            continue
        if line.strip().upper().startswith('DELIMITER '):
            if lines:
                raise ValueError('delimiter inside statement')
            delimiter = line.strip().split()[1]
            continue
        lines.append(line)
        if line.rstrip().endswith(delimiter):
            statements.append('\n'.join(lines).rstrip()[:-len(delimiter)].strip())
            lines = []
    if lines:
        raise ValueError('unterminated SQL: ' + str(path))
    return statements


def render(statements, intro=''):
    # Uniform delimiter keeps routine bodies intact in the ordinary mysql CLI.
    return intro + '\nDELIMITER $$\n' + '\n\n'.join(compact_sql(s.rstrip(';')) + '$$' for s in statements) + '\nDELIMITER ;\n'


def safe_create_tables(metadata):
    """Narrow import policy for untrusted SHOW CREATE TABLE data, never history.

    No supplied procedure, event, DML or historical script is ever executed.
    Reject external-table options, generated expressions and statement stacking.
    Only audited InnoDB column/index/FK/CHECK table bodies are accepted.
    """
    tables = metadata['ddl']
    if set(tables) != {r['TABLE_NAME'] for r in metadata['tables']}:
        raise ValueError('DDL/table metadata mismatch')
    known = set(tables)
    refs = {}
    for name, statement in tables.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name):
            raise ValueError('unsupported table identifier')
        if not statement.startswith(f'CREATE TABLE `{name}` (\n') or ';' in statement:
            raise ValueError('only one CREATE TABLE statement per table permitted')
        if not re.search(r'\) ENGINE=InnoDB DEFAULT CHARSET=\w+(?: COLLATE=\w+)?$', statement):
            raise ValueError('unsupported table options: ' + name)
        if re.search(r'\b(?:DATA DIRECTORY|INDEX DIRECTORY|TABLESPACE|CONNECTION|UNION|PARTITION|GENERATED|LOAD_FILE|SLEEP|BENCHMARK|INTO OUTFILE|DEFINER)\b|/\*|--', statement, re.I):
            raise ValueError('unsupported executable/table option: ' + name)
        for line in statement.splitlines()[1:-1]:
            if not re.match(r'  (?:`\w+` |PRIMARY KEY |(?:UNIQUE )?KEY |CONSTRAINT )', line):
                raise ValueError('unreviewed DDL shape: ' + name + ': ' + line)
        refs[name] = set(re.findall(r'REFERENCES `(\w+)`', statement)) - {name}
        if refs[name] - known:
            raise ValueError('external FK dependency: ' + name)
    result, done = [], set()
    while len(done) < len(tables):
        ready = sorted(n for n in tables if n not in done and refs[n] <= done)
        if not ready:
            raise ValueError('cyclic table creation dependencies require review')
        for name in ready:
            result.append(tables[name]); done.add(name)
    return result


def load_fence():
    # Imports only the standalone fence module, not application/config.
    path = BACKEND / 'cpm_back/services/student_deletion/fence.py'
    spec = importlib.util.spec_from_file_location('sql_bundle_fence', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shape_rows(metadata, key):
    """Stable comparable schema fields present in the supplied audit."""
    rows = copy.deepcopy(metadata[key])
    for row in rows:
        for name in ('TABLE_COLLATION', 'COLUMN_TYPE', 'DATA_TYPE','CHARACTER_SET_NAME','COLLATION_NAME'):
            if isinstance(row.get(name), str):
                row[name] = row[name].replace('utf8mb3', 'utf8')
        if key == 'routines':
            row['BODY_SHA256'] = hashlib.sha256(normalize(row.pop('ROUTINE_DEFINITION') or '').encode()).hexdigest()
        if key == 'triggers':
            row['BODY_SHA256'] = hashlib.sha256(normalize(row.pop('ACTION_STATEMENT') or '').encode()).hexdigest()
    return rows


SCHEMA = {
    'tables': ('information_schema.TABLES', 'TABLE_SCHEMA', ('TABLE_NAME','TABLE_TYPE','ENGINE','TABLE_COLLATION'), ('TABLE_NAME',)),
    'columns': ('information_schema.COLUMNS', 'TABLE_SCHEMA', ('TABLE_NAME','COLUMN_NAME','ORDINAL_POSITION','COLUMN_TYPE','DATA_TYPE','IS_NULLABLE','COLUMN_KEY','EXTRA','COLUMN_DEFAULT','CHARACTER_SET_NAME','COLLATION_NAME','GENERATION_EXPRESSION','SRS_ID','COLUMN_COMMENT'), ('TABLE_NAME','COLUMN_NAME')),
    'foreign_keys': ('''(SELECT k.TABLE_SCHEMA,k.TABLE_NAME,k.CONSTRAINT_NAME,k.COLUMN_NAME,k.ORDINAL_POSITION,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,r.UPDATE_RULE,r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.TABLE_NAME=k.TABLE_NAME AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME WHERE k.REFERENCED_TABLE_NAME IS NOT NULL)''', 'TABLE_SCHEMA', ('TABLE_NAME','CONSTRAINT_NAME','COLUMN_NAME','ORDINAL_POSITION','REFERENCED_TABLE_NAME','REFERENCED_COLUMN_NAME','UPDATE_RULE','DELETE_RULE'), ('TABLE_NAME','CONSTRAINT_NAME','COLUMN_NAME')),
    'indexes': ('information_schema.STATISTICS', 'TABLE_SCHEMA', ('TABLE_NAME','INDEX_NAME','NON_UNIQUE','SEQ_IN_INDEX','COLUMN_NAME','SUB_PART','INDEX_TYPE','IS_VISIBLE','EXPRESSION'), ('TABLE_NAME','INDEX_NAME','SEQ_IN_INDEX')),
    'routines': ('information_schema.ROUTINES', 'ROUTINE_SCHEMA', ('ROUTINE_NAME','ROUTINE_TYPE','BODY_SHA256'), ('ROUTINE_NAME','ROUTINE_TYPE')),
    'triggers': ('information_schema.TRIGGERS', 'TRIGGER_SCHEMA', ('TRIGGER_NAME','EVENT_OBJECT_TABLE','EVENT_MANIPULATION','ACTION_TIMING','BODY_SHA256'), ('TRIGGER_NAME',)),
}
NUMERIC = {'ORDINAL_POSITION','NON_UNIQUE','SEQ_IN_INDEX','SUB_PART','SRS_ID'}


def drift_query(metadata, key, table_filter=None):
    source, schema_field, fields, keys = SCHEMA[key]
    rows = shape_rows(metadata, key)
    if rows:
        fields=tuple(f for f in fields if any(f in r for r in rows))
    expected = [[r.get(k) for k in fields] for r in rows]
    fields_sql = ','.join(f'`{f}` '+('BIGINT' if f in NUMERIC else 'VARCHAR(2048)')+f" PATH '$[{n}]' NULL ON EMPTY NULL ON ERROR" for n,f in enumerate(fields))
    actual = []
    for field in fields:
        if field == 'BODY_SHA256':
            body = 'ROUTINE_DEFINITION' if key == 'routines' else 'ACTION_STATEMENT'
            actual.append(f"SHA2(TRIM(REPLACE(COALESCE({body},''),CONCAT(CHAR(13),CHAR(10)),CHAR(10))),256) AS BODY_SHA256")
        elif field in ('TABLE_COLLATION','COLUMN_TYPE','DATA_TYPE','CHARACTER_SET_NAME','COLLATION_NAME'):
            actual.append(f"REPLACE(`{field}`,'utf8mb3','utf8') AS `{field}`")
        else:
            actual.append('`'+field+'`')
    equal = ' AND '.join((f'a.`{f}` <=> e.`{f}`' if f in NUMERIC else f'BINARY a.`{f}` <=> BINARY e.`{f}`') for f in fields)
    scope_sql = '' if table_filter is None else ' AND TABLE_NAME IN ('+','.join(literal(t) for t in table_filter)+')'
    return (f"WITH expected AS (SELECT * FROM JSON_TABLE({literal(json.dumps(expected, separators=(',',':')))}, '$[*]' COLUMNS({fields_sql})) AS j), "
            f"actual AS (SELECT DISTINCT {','.join(actual)} FROM {source} s WHERE `{schema_field}`=DATABASE(){scope_sql}) "
            f"SELECT '{key}_missing_or_changed' AS check_name, COUNT(*) AS mismatch_count FROM expected e LEFT JOIN actual a ON {equal} WHERE a.`{keys[0]}` IS NULL "
            f"UNION ALL SELECT '{key}_unexpected_or_changed',COUNT(*) FROM actual a LEFT JOIN expected e ON {equal} WHERE e.`{keys[0]}` IS NULL")


def drift_queries(metadata, key):
    rows = metadata[key]
    if key not in ('columns','indexes','foreign_keys') or len(json.dumps(rows)) < 20000:
        return [drift_query(metadata,key)]
    groups, current, size = [], [], 0
    for table in sorted({r['TABLE_NAME'] for r in rows}):
        batch = [r for r in rows if r['TABLE_NAME']==table]
        length = len(json.dumps(batch))
        if current and size+length>20000:
            groups.append(current);current=[];size=0
        current.append(table);size+=length
    if current:groups.append(current)
    return [drift_query(dict(metadata, **{key:[r for r in rows if r['TABLE_NAME'] in group]}),key,group) for group in groups]


def count_check(label, query):
    return f"SELECT '{label}' AS check_name, ({query}) AS mismatch_count"


def anomaly_queries(metadata):
    queries = []
    for row in metadata['foreign_keys']:
        t,c,p,pc = (row[k] for k in ('TABLE_NAME','COLUMN_NAME','REFERENCED_TABLE_NAME','REFERENCED_COLUMN_NAME'))
        queries.append(count_check('fk_orphan:'+row['CONSTRAINT_NAME'], f'SELECT COUNT(*) FROM `{t}` c LEFT JOIN `{p}` p ON p.`{pc}`=c.`{c}` WHERE c.`{c}` IS NOT NULL AND p.`{pc}` IS NULL'))
    tables = {r['TABLE_NAME'] for r in metadata['tables']}
    for row in metadata['columns']:
        if row['COLUMN_NAME'] == 'student_id' and row['TABLE_NAME'] not in {'student_deletion_jobs','student_deletion_barriers','homework_student_purges'}:
            t = row['TABLE_NAME']
            queries.append(count_check('logical_student_orphan:'+t,f'SELECT COUNT(*) FROM `{t}` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL'))
    for table,role,ident in [('auth_users','role','ref_id'),('push_subscriptions','user_role','user_id'),('notifications','recipient_role','recipient_id'),('homework_chat_messages','sender_role','sender_id'),('homework_chat_reads','reader_role','reader_id'),('homework_realtime_presence','actor_role','actor_id')]:
        queries.append(count_check('role_student_orphan:'+table,f"SELECT COUNT(*) FROM `{table}` t LEFT JOIN students s ON s.id=t.`{ident}` WHERE t.`{role}`='student' AND (t.`{ident}` IS NULL OR s.id IS NULL)"))
    for table in ('homework_realtime_presence','homework_realtime_outbox','notifications'):
        queries.append(count_check('logical_thread_orphan:'+table,f'SELECT COUNT(*) FROM `{table}` t LEFT JOIN homework_chat_threads h ON h.id=t.thread_id WHERE t.thread_id IS NOT NULL AND h.id IS NULL'))
    for table,role,ident in [('homework_chat_messages','sender_role','sender_id'),('homework_chat_reads','reader_role','reader_id'),('homework_realtime_presence','actor_role','actor_id')]:
        queries.append(count_check('cross_student_thread:'+table,f"SELECT COUNT(*) FROM `{table}` t JOIN homework_chat_threads h ON h.id=t.thread_id WHERE t.`{role}`='student' AND t.`{ident}`<>h.student_id"))
    queries += [
        count_check('notification_owner_disagreement', "SELECT COUNT(*) FROM notifications n LEFT JOIN homework_chat_threads t ON t.id=n.thread_id WHERE (n.student_id IS NOT NULL AND t.student_id IS NOT NULL AND n.student_id<>t.student_id) OR (n.recipient_role='student' AND n.student_id IS NOT NULL AND n.recipient_id<>n.student_id) OR (n.recipient_role='student' AND t.student_id IS NOT NULL AND n.recipient_id<>t.student_id)"),
        count_check('homework_job_owner_disagreement', 'SELECT COUNT(*) FROM homework_file_jobs j JOIN homework_submissions s ON s.id=j.submission_id WHERE j.student_id<>s.student_id OR j.homework_id<>s.homework_id'),
        count_check('classic_attempt_owner_disagreement', 'SELECT COUNT(*) FROM classic_exam_attempts a JOIN classic_exam_assignments g ON g.id=a.assignment_id WHERE a.student_id<>g.student_id OR a.exam_id<>g.exam_id'),
        count_check('cross_schema_incoming_or_outgoing_fk', "SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE WHERE REFERENCED_TABLE_SCHEMA IS NOT NULL AND ((TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_SCHEMA<>DATABASE()) OR (REFERENCED_TABLE_SCHEMA=DATABASE() AND TABLE_SCHEMA<>DATABASE()))"),
        count_check('scheduled_events', 'SELECT COUNT(*) FROM information_schema.EVENTS WHERE EVENT_SCHEMA=DATABASE()'),
    ]
    for table,column in [('homework_submission_files','object_key'),('homework_file_jobs','staging_key')]:
        queries.append(count_check('non_ascii_historical_key:'+table,f'SELECT COUNT(*) FROM `{table}` WHERE `{column}` IS NOT NULL AND OCTET_LENGTH(`{column}`)<>CHAR_LENGTH(`{column}`)'))
    for field in ('current_file_id','draft_file_id'):
        queries.append(count_check('homework_'+field+'_owner_mismatch',f'SELECT COUNT(*) FROM homework_submissions s LEFT JOIN homework_submission_files f ON f.id=s.{field} WHERE s.{field} IS NOT NULL AND (f.id IS NULL OR f.submission_id<>s.id)'))
    queries.append(count_check('homework_job_result_owner_mismatch','SELECT COUNT(*) FROM homework_file_jobs j LEFT JOIN homework_submission_files f ON f.id=j.result_file_id WHERE j.result_file_id IS NOT NULL AND (f.id IS NULL OR f.submission_id<>j.submission_id)'))
    queries.append(count_check('outbox_unknown_event','SELECT COUNT(*) FROM homework_realtime_outbox WHERE BINARY event_type NOT IN (\'message.created\',\'message.read\',\'typing.changed\',\'submission.changed\',\'job.progress\',\'notification.created\')'))
    queries.append(count_check('outbox_nonobject_payload',"SELECT COUNT(*) FROM homework_realtime_outbox WHERE payload_json IS NOT NULL AND JSON_TYPE(payload_json)<>'OBJECT'"))
    queries.append(count_check('outbox_invalid_user_envelope',"SELECT COUNT(*) FROM homework_realtime_outbox WHERE BINARY event_type IN ('submission.changed','job.progress','notification.created') AND NOT COALESCE(thread_id IS NULL AND JSON_TYPE(payload_json)='OBJECT' AND JSON_LENGTH(payload_json)=2 AND JSON_CONTAINS_PATH(payload_json,'all','$.room','$.data') AND JSON_TYPE(JSON_EXTRACT(payload_json,'$.data'))='OBJECT' AND JSON_TYPE(JSON_EXTRACT(payload_json,'$.room'))='STRING' AND REGEXP_LIKE(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.room')),'^user:(student|admin|proctor|examinator|supervisor|staff_admin):[1-9][0-9]*$','c'),FALSE)"))
    queries.append(count_check('outbox_notification_entity_orphan',"SELECT COUNT(*) FROM homework_realtime_outbox o LEFT JOIN notifications n ON n.id=o.entity_id WHERE BINARY o.event_type='notification.created' AND n.id IS NULL"))
    queries.append(count_check('outbox_message_entity_orphan',"SELECT COUNT(*) FROM homework_realtime_outbox o LEFT JOIN homework_chat_messages m ON m.id=o.entity_id WHERE BINARY o.event_type='message.created' AND o.entity_id IS NOT NULL AND m.id IS NULL"))
    queries.append(count_check('outbox_message_thread_mismatch',"SELECT COUNT(*) FROM homework_realtime_outbox o JOIN homework_chat_messages m ON m.id=o.entity_id WHERE BINARY o.event_type IN ('message.created','message.read') AND o.thread_id IS NOT NULL AND o.thread_id<>m.thread_id"))
    return queries


def aggregate_file_queries():
    """Capture our read-only query builder without importing app/config."""
    path=BACKEND/'cpm_back/services/student_deletion/file_consistency.py'
    tree=ast.parse(path.read_text())
    nodes=[n for n in tree.body if isinstance(n,(ast.Assign,ast.FunctionDef))]
    namespace={'FileConsistencyError':RuntimeError}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),namespace)
    class Capture:
        def __init__(self):self.queries=[]
        def execute(self,query,params=()):
            if params:raise ValueError('expected global aggregate query')
            self.queries.append(query)
        def fetchone(self):return {'count':0}
    cursor=Capture()
    result=namespace['aggregate_file_consistency'](cursor,None,namespace['REQUIRED_COLUMNS'])
    return [count_check('file_consistency:'+key,query) for key,query in zip(result['counts'],cursor.queries)]


def gate(variable, reason):
    # Default stops before persistent writes. The documented batch invocation
    # must stop on SQL errors; --force / interactive SOURCE is unsupported.
    return [f"SET @cpm_gate_sql=IF(COALESCE(@{variable},0)=1,'SELECT 1 AS operator_acknowledged','SELECT * FROM information_schema.{reason}')", 'PREPARE cpm_gate FROM @cpm_gate_sql', 'EXECUTE cpm_gate', 'DEALLOCATE PREPARE cpm_gate']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--post-metadata', type=Path)
    parser.add_argument('--baseline-metadata', type=Path)
    parser.add_argument('--output', type=Path, default=DEST)
    args=parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    src=args.audit/'schema-metadata.json'; metadata=json.loads(src.read_text())
    ddl=safe_create_tables(metadata)
    fence=load_fence()
    baseline={k:copy.deepcopy(metadata[k]) for k in SCHEMA}
    enriched=args.baseline_metadata or args.output/'engine-baseline-shape.json'
    if enriched.exists():
        full=json.loads(enriched.read_text())
        # Match every original column attribute before extending defaults/charsets.
        original={(r['TABLE_NAME'],r['COLUMN_NAME']):r for r in baseline['columns']}
        for row in full['columns']:
            key=(row['TABLE_NAME'],row['COLUMN_NAME'])
            if key not in original or any(row.get(k)!=v for k,v in original[key].items()):
                raise ValueError('enriched baseline differs from supplied audit: '+repr(key))
        if len(full['columns'])!=len(original):raise ValueError('incomplete enriched column baseline')
        baseline['columns']=full['columns']
        (args.output/'engine-baseline-shape.json').write_text(json.dumps(full,ensure_ascii=False,indent=2)+'\n')
    (args.output/'expected-baseline.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2)+'\n')
    preflight=["SELECT DATABASE() AS selected_schema,VERSION() AS actual_server_version,@@version_comment AS actual_server_vendor,@@sql_mode AS sql_mode,@@lower_case_table_names AS lower_case_table_names", "SELECT 'Expected audit: 79 tables, 579 columns, 66 FK column rows, 193 indexes, 4 procedures; reported version 8.0.22-13. Any mismatch or inaccessible metadata blocks activation.' AS operator_instruction"]
    preflight += [q for k in SCHEMA for q in drift_queries(baseline,k)]
    preflight += anomaly_queries(metadata)
    (args.output/'000_preflight_read_only.sql').write_text(render(preflight,'-- READ ONLY. Default and first step. Outputs metadata and aggregate counts only.\n-- Select the intended database in the mysql command. Do not activate on unexplained nonzero counts.'))
    prep_paths=[BACKEND/'migrations/030_student_deletion_jobs.sql',HOMEWORK/'migrations/002_student_purges.sql']
    prepare=gate('cpm_deletion_prepare_confirmed','CPM_PREPARATION_REQUIRES_REVIEWED_PREFLIGHT')
    for path in prep_paths: prepare+=split_sql(path)
    (args.output/'030_002_prepare.sql').write_text(render(prepare,'-- PERSISTENT PREPARATION; no student deletion. Requires explicit session acknowledgement.\n-- This is not an old-application compatibility guarantee. Coordinate all service deployment.\n-- Backend 030 then homework 002; audited database already contains homework 001 tables.'))
    activation=gate('cpm_deletion_maintenance_confirmed','CPM_STOP_ALL_WRITERS_BEFORE_ACTIVATION')
    activation += fence._migration_statements()
    present={r['TABLE_NAME'] for r in metadata['tables']} | {'homework_object_ownership'}
    guarded=(set(fence.DIRECT_TABLES)|set(fence.INDIRECT_TABLES)|fence.SPECIAL_TABLES|set(fence.JOURNAL_COLUMNS)) & present
    triggers=list(fence._trigger_definitions(guarded))
    for name,table,event,timing,body in triggers:
        activation += [f'DROP TRIGGER IF EXISTS `{name}`', f'CREATE TRIGGER `{name}` {timing} {event} ON `{table}` FOR EACH ROW {body}']
    declared={re.match(r'CREATE PROCEDURE (\w+)',q).group(1) for q in activation if q.startswith('CREATE PROCEDURE ')}
    calls=set(re.findall(r'CALL (cpm_assert_student_\w+)\s*\(', ' '.join(activation)))
    if calls-declared:raise ValueError('incomplete migration/fence source: missing routines '+','.join(sorted(calls-declared)))
    (args.output/'031_activate_all_write_guards.sql').write_text(render(activation,'-- STOPPED-WRITER ACTIVATION. DDL auto-commits: keep every writer stopped on any failure.\n-- Complete literal SQL, including every schema-specific per-table trigger.\n-- Never run with --force. Do not enable deletion until 032 passes and all app checks pass.'))
    if args.post_metadata:
        post=json.loads(args.post_metadata.read_text())
        # Existing procedures are not installed/executed by the local engine;
        # retain their audited expected definitions for the operator drift check.
        legacy_names={r['ROUTINE_NAME'] for r in baseline['routines']}
        post['routines']=[r for r in post['routines'] if not r['ROUTINE_NAME'].startswith('cpm_test_') and r['ROUTINE_NAME'] not in legacy_names]+baseline['routines']
        post['triggers']=[r for r in post['triggers'] if not r['TRIGGER_NAME'].startswith('cpm_test_')]
        (args.output/'expected-post-activation.json').write_text(json.dumps(post,ensure_ascii=False,indent=2)+'\n')
    elif (args.output/'expected-post-activation.json').exists():
        post=json.loads((args.output/'expected-post-activation.json').read_text())
    else:
        post=None
    if post:
        checks=[q for k in SCHEMA for q in drift_queries(post,k)]
        checks += [count_check('guard_singleton_version', f"SELECT IF(COUNT(*)=1 AND MIN(schema_version)={getattr(fence,'GUARD_SCHEMA_VERSION',1)},0,1) FROM student_deletion_guard_state WHERE id=1"),count_check('guard_singleton_extra_rows','SELECT COUNT(*) FROM student_deletion_guard_state WHERE id<>1')]
        checks+=anomaly_queries(metadata)
        checks+=aggregate_file_queries()
        (args.output/'032_verify_and_detect_drift.sql').write_text(render(checks,'-- READ ONLY. Every mismatch_count must be zero before enabling deletion.\n-- Repeat for drift monitoring; preserve original four procedure bodies.'))
    sources={str(p.relative_to(BACKEND)) if p.is_relative_to(BACKEND) else '../cpm-homework-service/'+str(p.relative_to(HOMEWORK)):digest(p) for p in prep_paths+[BACKEND/'migrations/031_student_deletion_write_barriers.sql',BACKEND/'cpm_back/services/student_deletion/fence.py',Path(__file__),BACKEND/'cpm_back/services/student_deletion/file_consistency.py']}
    source_audit={str(p.relative_to(args.audit)):digest(p) for p in sorted(args.audit.rglob('*')) if p.is_file()}
    chunks=[]
    chunk_dir=args.output/'user-sql';chunk_dir.mkdir(exist_ok=True)
    for old in chunk_dir.glob('*.sql'):old.unlink()
    for full in sorted(args.output.glob('*.sql')):
        batches=[];batch=[]
        for statement in split_sql(full):
            candidate=render(batch+[statement])
            if len(candidate)>74000 and batch:
                batches.append(batch);batch=[]
            if len(render([statement]))>74000:raise ValueError('statement too large for chat chunk: '+full.name)
            batch.append(statement)
        if batch:batches.append(batch)
        for n,batch in enumerate(batches,1):
            name=full.stem+f'.part{n:02d}.sql'
            text=render(batch, '-- COMPLETE LITERAL SQL: '+full.name+f', part {n}/{len(batches)}.\n-- Read README before mutating phases. Run parts in numerical order.\n')
            (chunk_dir/name).write_text(text)
            chunks.append({'file':'user-sql/'+name,'characters':len(text),'sha256':digest(chunk_dir/name)})
    manifest={'format_version':2,'user_sql_chunks':chunks,'audit_reported_server_version':metadata['version'][0]['version'],'baseline_counts':{'tables':len(metadata['tables']),'columns':len(metadata['columns']),'foreign_key_column_rows':len(metadata['foreign_keys']),'indexes':len({(x['TABLE_NAME'],x['INDEX_NAME']) for x in metadata['indexes']}),'index_column_rows':len(metadata['indexes']),'routines':len(metadata['routines'])},'generated_guarded_tables':sorted(guarded),'generated_table_trigger_count':len(triggers),'source_sha256':sources,'audit_input_sha256':source_audit,'execution_boundary':'Supplied table DDL only after restricted shape validation; supplied history and existing routines never executed. No database credentials or application configuration loaded.'}
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(args.output),'guarded_tables':len(guarded),'table_triggers':len(triggers),'post_metadata_included':bool(post)}))


if __name__=='__main__': main()
