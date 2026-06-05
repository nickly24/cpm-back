#!/usr/bin/env python3
"""
Миграция 009: user_import_sessions, user_import_jobs, user_import_job_results.

Запуск из cpm-back:
  python3 scripts/apply_migration_009_user_import.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mysql.connector
from cpm_back.config import config


def table_exists(cursor, table):
    cursor.execute(
        """
        SELECT COUNT(*) FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table,),
    )
    return cursor.fetchone()[0] > 0


def main():
    print(
        f"Подключение: {config.MYSQL_HOST}:{config.MYSQL_PORT}/{config.MYSQL_DATABASE} "
        f"({config.MYSQL_USER})"
    )
    conn = mysql.connector.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        autocommit=False,
    )
    cursor = conn.cursor()

    try:
        if table_exists(cursor, "user_import_sessions"):
            print("user_import_sessions уже есть — пропуск")
        else:
            print("Создаём user_import_sessions…")
            cursor.execute(
                """
                CREATE TABLE user_import_sessions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    import_type VARCHAR(64) NOT NULL DEFAULT 'users',
                    source_filename VARCHAR(255) NULL,
                    preview_payload JSON NOT NULL,
                    created_by INT NULL,
                    created_by_name VARCHAR(255) NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at DATETIME NOT NULL,
                    INDEX idx_user_import_sessions_expires (expires_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )

        if table_exists(cursor, "user_import_jobs"):
            print("user_import_jobs уже есть — пропуск")
        else:
            print("Создаём user_import_jobs…")
            cursor.execute(
                """
                CREATE TABLE user_import_jobs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    session_id INT NOT NULL,
                    import_type VARCHAR(64) NOT NULL DEFAULT 'users',
                    status ENUM('queued', 'running', 'rolling_back', 'completed', 'failed')
                        NOT NULL DEFAULT 'queued',
                    created_by INT NULL,
                    created_by_name VARCHAR(255) NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at DATETIME NULL,
                    completed_at DATETIME NULL,
                    total_rows INT NOT NULL DEFAULT 0,
                    processed_count INT NOT NULL DEFAULT 0,
                    successful INT NOT NULL DEFAULT 0,
                    skipped INT NOT NULL DEFAULT 0,
                    failed INT NOT NULL DEFAULT 0,
                    message TEXT NULL,
                    errors JSON NULL,
                    entities_created JSON NULL,
                    summary JSON NULL,
                    INDEX idx_user_import_jobs_status (status),
                    INDEX idx_user_import_jobs_created_at (created_at),
                    INDEX idx_user_import_jobs_session (session_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )

        if table_exists(cursor, "user_import_job_results"):
            print("user_import_job_results уже есть — пропуск")
        else:
            print("Создаём user_import_job_results…")
            cursor.execute(
                """
                CREATE TABLE user_import_job_results (
                    job_id INT NOT NULL PRIMARY KEY,
                    rows_data JSON NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT fk_user_import_job_results_job
                        FOREIGN KEY (job_id) REFERENCES user_import_jobs(id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )

        conn.commit()
        print("\nМиграция 009 применена успешно.")
    except Exception as exc:
        conn.rollback()
        print(f"Ошибка: {exc}")
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
