#!/usr/bin/env python3
"""
Миграция 013: тренировки v2 (directions, student_card_progress, study settings).

Запуск из cpm-back-main:
  python3 scripts/apply_migration_013_training_unification.py
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

import mysql.connector

ROOT = Path(__file__).resolve().parents[1]
config = runpy.run_path(str(ROOT / "cpm_back" / "config.py"))["config"]


def column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (table, column),
    )
    return cursor.fetchone()[0] > 0


def table_exists(cursor, table: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*) FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table,),
    )
    return cursor.fetchone()[0] > 0


def fk_exists(cursor, table: str, fk_name: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND CONSTRAINT_NAME = %s
          AND CONSTRAINT_TYPE = 'FOREIGN KEY'
        """,
        (table, fk_name),
    )
    return cursor.fetchone()[0] > 0


def index_exists(cursor, table: str, index_name: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = %s
        """,
        (table, index_name),
    )
    return cursor.fetchone()[0] > 0


def already_applied(cursor) -> bool:
    return (
        table_exists(cursor, "student_card_progress")
        and column_exists(cursor, "card_themes", "direction_id")
        and not table_exists(cursor, "training_sections")
    )


def run_step(cursor, label: str, sql: str) -> None:
    print(f"  {label}")
    cursor.execute(sql)


def main() -> int:
    print(
        f"Подключение: {config.MYSQL_HOST}:{config.MYSQL_PORT}/"
        f"{config.MYSQL_DATABASE} ({config.MYSQL_USER})"
    )
    conn = mysql.connector.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        autocommit=True,
    )
    cursor = conn.cursor()

    try:
        if already_applied(cursor):
            print("Миграция 013 уже применена — пропуск")
            return 0

        print("Применяем миграцию 013 …")

        for table in ("student_progress", "cards", "card_themes"):
            run_step(cursor, f"DELETE FROM {table}", f"DELETE FROM {table}")

        if not table_exists(cursor, "student_card_progress"):
            run_step(
                cursor,
                "CREATE student_card_progress",
                """
                CREATE TABLE student_card_progress (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    student_id INT NOT NULL,
                    section_kind ENUM('manual', 'test') NOT NULL,
                    section_ref_id VARCHAR(24) NOT NULL,
                    card_ref VARCHAR(64) NOT NULL,
                    content_fingerprint CHAR(64) NOT NULL,
                    learned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_student_card_ref (student_id, card_ref),
                    INDEX idx_scp_student_section (student_id, section_kind, section_ref_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )

        if not table_exists(cursor, "student_section_study_settings"):
            run_step(
                cursor,
                "CREATE student_section_study_settings",
                """
                CREATE TABLE student_section_study_settings (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    student_id INT NOT NULL,
                    section_kind ENUM('manual', 'test') NOT NULL,
                    section_ref_id VARCHAR(24) NOT NULL,
                    batch_size INT NOT NULL DEFAULT 10,
                    last_batch_index INT NULL,
                    study_mode ENUM('all', 'unlearned', 'learned', 'stale') NOT NULL DEFAULT 'unlearned',
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_student_section_settings (student_id, section_kind, section_ref_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            )

        if not column_exists(cursor, "cards", "sort_order"):
            run_step(
                cursor,
                "ADD cards.sort_order",
                """
                ALTER TABLE cards
                    ADD COLUMN sort_order INT NOT NULL DEFAULT 0 COMMENT 'Порядок в разделе' AFTER theme_id
                """,
            )

        if column_exists(cursor, "card_themes", "section_id"):
            if fk_exists(cursor, "card_themes", "fk_card_themes_section"):
                run_step(
                    cursor,
                    "DROP FK fk_card_themes_section",
                    "ALTER TABLE card_themes DROP FOREIGN KEY fk_card_themes_section",
                )
            for index_name in ("idx_card_themes_section_id", "idx_card_themes_section"):
                if index_exists(cursor, "card_themes", index_name):
                    run_step(
                        cursor,
                        f"DROP INDEX {index_name}",
                        f"ALTER TABLE card_themes DROP INDEX {index_name}",
                    )
            run_step(
                cursor,
                "DROP card_themes.section_id",
                "ALTER TABLE card_themes DROP COLUMN section_id",
            )

        if not column_exists(cursor, "card_themes", "direction_id"):
            run_step(
                cursor,
                "ADD card_themes.direction_id",
                """
                ALTER TABLE card_themes
                    ADD COLUMN direction_id INT NOT NULL COMMENT 'FK directions.id' AFTER name,
                    ADD INDEX idx_card_themes_direction_id (direction_id)
                """,
            )

        if table_exists(cursor, "training_sections"):
            run_step(
                cursor,
                "DROP training_sections",
                "DROP TABLE training_sections",
            )

        if not already_applied(cursor):
            raise RuntimeError("Миграция 013 завершилась, но схема не соответствует ожиданиям")

        print("\nМиграция 013 применена успешно.")
        return 0
    except Exception as exc:
        print(f"Ошибка: {exc}")
        return 1
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
