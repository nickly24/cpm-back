"""Apply the additive admin-role migration; --check performs metadata reads only.

Loads configuration directly, never starts Flask, workers, MongoDB or the Telegram bot.
"""
import argparse
import json
import runpy
from pathlib import Path

import mysql.connector

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ENUM = "enum('student','proctor','admin','examinator','supervisor')"
NEW_ENUM = "enum('student','proctor','admin','examinator','supervisor','staff_admin')"
TABLES = ('admin_roles', 'admin_role_permissions', 'admin_role_users')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    config = runpy.run_path(str(ROOT / 'cpm_back/config.py'))['config']
    conn = mysql.connector.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT, user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD, database=config.MYSQL_DATABASE,
        connection_timeout=10, autocommit=False,
    )
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='auth_users' AND COLUMN_NAME='role'")
        actual = cursor.fetchone()['COLUMN_TYPE']
        if actual not in (EXPECTED_ENUM, NEW_ENUM):
            raise RuntimeError('Unexpected auth_users.role definition; migration stopped before DDL')
        if not args.check:
            cursor.execute('SET SESSION lock_wait_timeout=10')
            if actual == EXPECTED_ENUM:
                cursor.execute(f'ALTER TABLE auth_users MODIFY COLUMN role {NEW_ENUM} NOT NULL, ALGORITHM=INPLACE, LOCK=NONE')
            sql = (ROOT / 'migrations/014_admin_roles.sql').read_text()
            for statement in sql.split(';'):
                if statement.strip():
                    cursor.execute(statement)
            conn.commit()
        cursor.execute("SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('admin_roles','admin_role_permissions','admin_role_users') ORDER BY TABLE_NAME,ORDINAL_POSITION")
        columns = cursor.fetchall()
        cursor.execute("SELECT COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='auth_users' AND COLUMN_NAME='role'")
        final_enum = cursor.fetchone()['COLUMN_TYPE']
        present = sorted({row['TABLE_NAME'] for row in columns})
        if final_enum != NEW_ENUM or present != sorted(TABLES):
            raise RuntimeError('Migration schema is incomplete')
        cursor.execute("SELECT TABLE_NAME,CONSTRAINT_TYPE,CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME IN ('admin_roles','admin_role_permissions','admin_role_users')")
        constraints = cursor.fetchall()
        print(json.dumps({'mode': 'check' if args.check else 'apply', 'schema': 'verified', 'tables': present, 'column_count':len(columns), 'constraints':constraints, 'existing_business_rows_modified':False}, ensure_ascii=False))
    finally:
        conn.rollback()
        cursor.close()
        conn.close()


if __name__ == '__main__':
    main()
