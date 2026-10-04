"""Offline real-MySQL SQL engine test harness; never opens a network/socket server."""
import argparse
import hashlib
import json
from pathlib import Path
import os
import re
import subprocess
import uuid
import tempfile

RUNTIME = Path(os.environ.get('CPM_SQL_TEST_OUTPUT', str(Path(tempfile.gettempdir()) / 'cpm-student-deletion-sql'))).resolve()
MYSQL = Path(os.environ['CPM_TEST_MYSQL_BASE_DIR']).resolve()

def split_sql(path):
    """Parse repository migration delimiter directives, preserving routine bodies."""
    statements, buffer, delimiter = [], [], ';'
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('--'):
            continue
        if line.strip().upper().startswith('DELIMITER '):
            if buffer:
                raise ValueError('Delimiter directive inside pending statement')
            delimiter = line.strip().split()[1]
            continue
        buffer.append(line)
        if line.rstrip().endswith(delimiter):
            statements.append('\n'.join(buffer).rstrip()[:-len(delimiter)].strip())
            buffer = []
    if buffer:
        raise ValueError('Unterminated SQL statement in ' + str(path))
    return statements

def run(statements, name='schema'):
    if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
        raise ValueError('Invalid test label')
    if not (MYSQL / 'bin/mysqld').is_file():
        raise ValueError('CPM_TEST_MYSQL_BASE_DIR must contain bin/mysqld')
    version = subprocess.run([str(MYSQL / 'bin/mysqld'), '--no-defaults', '--version'], capture_output=True, text=True, check=True).stdout
    if not re.search(r'\bVer 8\.0\.(?:22|46)\b', version):
        raise ValueError('This fixture requires official MySQL 8.0.22 or 8.0.46')
    RUNTIME.mkdir(parents=True, exist_ok=True)
    workspace = RUNTIME / ('engine_' + name + '_' + uuid.uuid4().hex[:10])
    workspace.mkdir()
    data = workspace / 'data'
    data.mkdir()
    output = workspace / 'out'
    output.mkdir()
    sql = workspace / 'init.sql'
    # Bootstrap parser supports DELIMITER $$; this preserves semicolons in routines.
    lines = ['DELIMITER $$']
    for n, statement in enumerate(statements, 1):
        quoted = re.split(r"('(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"|`(?:``|[^`])*`)", statement.replace('@@LOCAL_OUTPUT_DIR@@', str(output)).strip().rstrip(';'))
        compact = ''.join(piece if n % 2 else re.sub(r'\s+', ' ', piece) for n, piece in enumerate(quoted))
        lines.append(compact + '$$')
        lines.append(f"SELECT 'ok' INTO OUTFILE '{output}/statement_{n:04d}.ok'$$")
    sql.write_text('\n'.join(lines) + '\n')
    log = workspace / 'mysql.log'
    env = dict(os.environ)
    cmd = [str(MYSQL/'bin/mysqld'), '--no-defaults', '--initialize-insecure',
           '--skip-networking', '--mysqlx=0', '--init-file='+str(sql), '--basedir='+str(MYSQL), '--datadir='+str(data),
           '--secure-file-priv='+str(output), '--log-error='+str(log),
           '--innodb-buffer-pool-size=32M']
    cmd.append('--innodb-log-file-size=8M' if '8.0.22' in version else '--innodb-redo-log-capacity=16M')
    (workspace/'runtime.json').write_text(json.dumps({'version':version.strip(),'binary_sha256':hashlib.sha256((MYSQL/'bin/mysqld').read_bytes()).hexdigest(),'socket_free':True,'mode':'initialize-insecure with init-file; no server listener'},indent=2)+'\n')
    process = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    text = log.read_text() if log.exists() else process.stderr
    errors = [line for line in text.splitlines() if '[ERROR]' in line]
    print('Run:', workspace)
    print('SQL statements:', len(statements))
    print('Exit:', process.returncode)
    completed = len(list(output.glob('statement_*.ok')))
    print('Completed statements:', completed)
    if completed < len(statements):
        print('Failing statement:', statements[completed][:2000])
    if errors:
        print('\n'.join(errors))
    if process.returncode or errors or completed != len(statements):
        raise RuntimeError('MySQL initialization SQL test failed')
    return workspace

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sql', type=Path)
    args=parser.parse_args()
    run(split_sql(args.sql))
