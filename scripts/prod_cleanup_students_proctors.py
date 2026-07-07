#!/usr/bin/env python3
"""
Delete all students, proctors, and their linked production data.

Default mode is dry-run. Execute only with:
  --execute --confirm DELETE_STUDENTS_PROCTORS_PROD
"""
from __future__ import annotations

import argparse
import json
import runpy
from datetime import date, datetime
from decimal import Decimal

import mysql.connector
from pymongo import MongoClient

CONFIRM_PHRASE = "DELETE_STUDENTS_PROCTORS_PROD"

MYSQL_TABLES = [
    ("homework_sessions", "student_id"),
    ("attendance", "student_id"),
    ("class_day_attendance", "student_id"),
    ("exam_sessions", "student_id"),
    ("test_sessions", "student_id"),
    ("Allratings", "student_id"),
    ("student_card_progress", "student_id"),
    ("student_section_study_settings", "student_id"),
    ("zaps", "student_id"),
]

MONGO_TARGETS = [
    ("test_sessions", "studentId"),
    ("test_attempts", "studentId"),
    ("rate_rec", "student_id"),
]


def json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)

def load_config():
    return runpy.run_path("cpm_back/config.py")["config"]


def mysql_connect(config):
    return mysql.connector.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        autocommit=False,
        connection_timeout=10,
    )


def placeholders(values):
    return ", ".join(["%s"] * len(values))


def fetch_all(cursor, query, params=()):
    cursor.execute(query, params)
    return cursor.fetchall()


def count_all(cursor, student_ids, proctor_ids):
    counts = {}
    counts["students"] = len(student_ids)
    counts["proctors"] = len(proctor_ids)

    if student_ids:
        marker = placeholders(student_ids)
        for table, column in MYSQL_TABLES:
            cursor.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {column} IN ({marker})", tuple(student_ids))
            counts[table] = int(cursor.fetchone()["c"])
        cursor.execute(
            f"SELECT COUNT(*) AS c FROM zap_dates WHERE zap_id IN "
            f"(SELECT id FROM zaps WHERE student_id IN ({marker}))",
            tuple(student_ids),
        )
        counts["zap_dates"] = int(cursor.fetchone()["c"])
        cursor.execute(
            f"SELECT COUNT(*) AS c FROM zap_img WHERE zap_id IN "
            f"(SELECT id FROM zaps WHERE student_id IN ({marker}))",
            tuple(student_ids),
        )
        counts["zap_img"] = int(cursor.fetchone()["c"])
    else:
        for table, _ in MYSQL_TABLES:
            counts[table] = 0
        counts["zap_dates"] = 0
        counts["zap_img"] = 0

    if student_ids:
        marker = placeholders(student_ids)
        cursor.execute(
            f"SELECT COUNT(*) AS c FROM auth_users WHERE role = 'student' AND ref_id IN ({marker})",
            tuple(student_ids),
        )
        counts["auth_users_student"] = int(cursor.fetchone()["c"])
    else:
        counts["auth_users_student"] = 0

    if proctor_ids:
        marker = placeholders(proctor_ids)
        cursor.execute(
            f"SELECT COUNT(*) AS c FROM auth_users WHERE role = 'proctor' AND ref_id IN ({marker})",
            tuple(proctor_ids),
        )
        counts["auth_users_proctor"] = int(cursor.fetchone()["c"])
    else:
        counts["auth_users_proctor"] = 0

    cursor.execute("SELECT COUNT(*) AS c FROM rating_recalc_jobs")
    counts["rating_recalc_jobs"] = int(cursor.fetchone()["c"])
    return counts


def delete_mysql(cursor, student_ids, proctor_ids):
    deleted = {}
    if student_ids:
        marker = placeholders(student_ids)
        cursor.execute(
            f"DELETE FROM zap_img WHERE zap_id IN "
            f"(SELECT id FROM zaps WHERE student_id IN ({marker}))",
            tuple(student_ids),
        )
        deleted["zap_img"] = cursor.rowcount
        cursor.execute(
            f"DELETE FROM zap_dates WHERE zap_id IN "
            f"(SELECT id FROM zaps WHERE student_id IN ({marker}))",
            tuple(student_ids),
        )
        deleted["zap_dates"] = cursor.rowcount
        for table, column in MYSQL_TABLES:
            cursor.execute(f"DELETE FROM {table} WHERE {column} IN ({marker})", tuple(student_ids))
            deleted[table] = cursor.rowcount
        cursor.execute(
            f"DELETE FROM auth_users WHERE role = 'student' AND ref_id IN ({marker})",
            tuple(student_ids),
        )
        deleted["auth_users_student"] = cursor.rowcount
        cursor.execute(f"DELETE FROM students WHERE id IN ({marker})", tuple(student_ids))
        deleted["students"] = cursor.rowcount

    if proctor_ids:
        marker = placeholders(proctor_ids)
        cursor.execute(
            f"DELETE FROM auth_users WHERE role = 'proctor' AND ref_id IN ({marker})",
            tuple(proctor_ids),
        )
        deleted["auth_users_proctor"] = cursor.rowcount
        cursor.execute(f"DELETE FROM proctors WHERE id IN ({marker})", tuple(proctor_ids))
        deleted["proctors"] = cursor.rowcount

    cursor.execute("DELETE FROM rating_recalc_jobs")
    deleted["rating_recalc_jobs"] = cursor.rowcount
    return deleted


def delete_mongo(db, student_ids):
    deleted = {}
    student_str_ids = [str(value) for value in student_ids]
    collections = set(db.list_collection_names())
    for collection_name, field in MONGO_TARGETS:
        if collection_name not in collections:
            deleted[collection_name] = 0
            continue
        query = {"$or": [{field: {"$in": student_ids}}, {field: {"$in": student_str_ids}}]}
        result = db[collection_name].delete_many(query)
        deleted[collection_name] = result.deleted_count
    return deleted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    config = load_config()

    mysql_conn = mysql_connect(config)
    mongo_client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=10000)
    mongo_db = mongo_client[config.MONGODB_DB_NAME]

    try:
        cursor = mysql_conn.cursor(dictionary=True)
        student_ids = [int(row["id"]) for row in fetch_all(cursor, "SELECT id FROM students ORDER BY id")]
        proctor_ids = [int(row["id"]) for row in fetch_all(cursor, "SELECT id FROM proctors ORDER BY id")]
        mysql_counts = count_all(cursor, student_ids, proctor_ids)

        student_str_ids = [str(value) for value in student_ids]
        mongo_counts = {}
        collections = set(mongo_db.list_collection_names())
        for collection_name, field in MONGO_TARGETS:
            if collection_name in collections:
                query = {"$or": [{field: {"$in": student_ids}}, {field: {"$in": student_str_ids}}]}
                mongo_counts[collection_name] = mongo_db[collection_name].count_documents(query)
            else:
                mongo_counts[collection_name] = 0

        print("MYSQL_COUNTS")
        print(json.dumps(mysql_counts, ensure_ascii=False, indent=2, default=json_default))
        print("MONGO_COUNTS")
        print(json.dumps(mongo_counts, ensure_ascii=False, indent=2, default=json_default))

        if not args.execute:
            print("DRY_RUN_ONLY")
            return
        if args.confirm != CONFIRM_PHRASE:
            raise SystemExit(f"Refusing execute without --confirm {CONFIRM_PHRASE}")

        mysql_deleted = delete_mysql(cursor, student_ids, proctor_ids)
        mysql_conn.commit()
        mongo_deleted = delete_mongo(mongo_db, student_ids)

        summary = {
            "mysql_deleted": mysql_deleted,
            "mongo_deleted": mongo_deleted,
        }
        print("DELETED")
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default))
    except Exception:
        mysql_conn.rollback()
        raise
    finally:
        mysql_conn.close()
        mongo_client.close()


if __name__ == "__main__":
    main()
