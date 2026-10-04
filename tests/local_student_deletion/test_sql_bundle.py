"""No-network structural checks for the literal SQL deliverable."""
import importlib.util
import json
import os
from pathlib import Path
import re

BACKEND=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('bundle_generator',BACKEND/'scripts/generate_student_deletion_sql.py')
gen=importlib.util.module_from_spec(spec);spec.loader.exec_module(gen)


def test_default_preflight_is_read_only():
    assert all(q.startswith(('SELECT ','WITH ')) for q in gen.split_sql(gen.DEST/'000_preflight_read_only.sql'))
    assert all(q.startswith(('SELECT ','WITH ')) for q in gen.split_sql(gen.DEST/'032_verify_and_detect_drift.sql'))


def test_all_chat_parts_are_complete_and_bounded():
    manifest=json.loads((gen.DEST/'manifest.json').read_text())
    for row in manifest['user_sql_chunks']:
        path=gen.DEST/row['file']
        assert len(path.read_text())==row['characters']<80000
        assert gen.digest(path)==row['sha256']
    for full in gen.DEST.glob('*.sql'):
        parts=sorted((gen.DEST/'user-sql').glob(full.stem+'.part*.sql'))
        assert parts
        assert gen.split_sql(full)==[q for p in parts for q in gen.split_sql(p)]


def test_activation_has_every_concrete_generated_trigger():
    manifest=json.loads((gen.DEST/'manifest.json').read_text())
    fence=gen.load_fence()
    statements=gen.split_sql(gen.DEST/'031_activate_all_write_guards.sql')
    actual=[q for q in statements if q.startswith('CREATE TRIGGER `cpm_sd_')]
    expected=list(fence._trigger_definitions(manifest['generated_guarded_tables']))
    assert len(actual)==len(expected)==manifest['generated_table_trigger_count']
    for name,table,event,timing,body in expected:
        sql=f'CREATE TRIGGER `{name}` {timing} {event} ON `{table}` FOR EACH ROW {body}'
        assert gen.compact_sql(sql) in actual


def test_mutating_files_require_explicit_session_acknowledgement():
    for name,variable in [('030_002_prepare.sql','cpm_deletion_prepare_confirmed'),('031_activate_all_write_guards.sql','cpm_deletion_maintenance_confirmed')]:
        q=gen.split_sql(gen.DEST/name)
        assert q[0].startswith('SET @cpm_gate_sql=IF(COALESCE(@'+variable+',0)=1')
        assert q[1:4]==['PREPARE cpm_gate FROM @cpm_gate_sql','EXECUTE cpm_gate','DEALLOCATE PREPARE cpm_gate']


def test_exact_body_hashes_do_not_erase_literal_case_or_spacing():
    assert gen.normalize("BEGIN SELECT 'student'; END")!=gen.normalize("BEGIN SELECT 'Student'; END")
    assert gen.normalize("BEGIN SELECT 'two  spaces'; END")!=gen.normalize("BEGIN SELECT 'two spaces'; END")
    assert gen.compact_sql("BEGIN\n SELECT 'two  spaces';\n END")=="BEGIN SELECT 'two  spaces'; END"


def test_source_hashes_match_current_files():
    manifest=json.loads((gen.DEST/'manifest.json').read_text())
    for name,value in manifest['source_sha256'].items():
        assert gen.digest(BACKEND/name)==value,name


def test_audited_baseline_is_complete():
    metadata=json.loads((gen.DEST/'expected-baseline.json').read_text())
    assert len(metadata['tables'])==79
    assert len(metadata['columns'])==579
    assert len(metadata['foreign_keys'])==66
    assert len({(r['TABLE_NAME'],r['INDEX_NAME']) for r in metadata['indexes']})==193
    assert len(metadata['routines'])==4
    assert not metadata['triggers']
    assert all('COLUMN_DEFAULT' in r and 'COLLATION_NAME' in r for r in metadata['columns'])


def test_untrusted_stacked_sql_is_rejected():
    bad={'tables':[{'TABLE_NAME':'bad'}],'ddl':{'bad':'CREATE TABLE `bad` (\n  `id` int\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4; DROP DATABASE something'}}
    try:gen.safe_create_tables(bad)
    except ValueError:pass
    else:raise AssertionError('stacked untrusted statement accepted')
