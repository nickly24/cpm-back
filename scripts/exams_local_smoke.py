#!/usr/bin/env python3
"""Disposable, loopback-only exam UI/API sandbox. Never loads a prod connection.

Requires an explicit local MySQL Unix socket. Creates a unique empty schema,
seeds synthetic identities, and drops ONLY that schema on normal shutdown.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.exams_migrations import REQUIRED_BASE_TABLES, run_migrations, identifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, default=8009)
    args = parser.parse_args()
    if not Path(args.socket).is_absolute() or not 1024 <= args.port <= 65535:
        parser.error("Need an absolute local Unix socket and unprivileged port")
    import mysql.connector
    from flask import Flask
    from flask_cors import CORS
    from werkzeug.security import generate_password_hash
    from cpm_back.db import mysql_pool
    from cpm_back.auth.jwt_auth import generate_token
    from cpm_back.auth.admin_permissions import enforce_admin_permissions
    from cpm_back.blueprints.auth_bp import auth_bp
    from cpm_back.blueprints.directions_bp import directions_bp
    from cpm_back.blueprints.exams_v2_bp import exams_v2_bp
    from cpm_back.blueprints.examiner_exams_bp import examiner_exams_bp
    from cpm_back.blueprints.student_exams_bp import student_exams_bp
    from cpm_back.blueprints.exam_imports_bp import exam_imports_bp

    database = "cpm_exam_smoke_" + uuid.uuid4().hex[:16]
    connection = mysql.connector.connect(
        unix_socket=args.socket, user="root", password="", autocommit=False
    )
    cursor = connection.cursor()
    cursor.execute(
        "CREATE DATABASE "
        + identifier(database)
        + " CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    try:
        cursor.execute("USE " + identifier(database))
        schema = json.loads(
            (
                ROOT
                / "docs/classic-exams-implementation/2026-09-12-prod-preflight.json"
            ).read_text()
        )["schema"]
        for table in REQUIRED_BASE_TABLES:
            cursor.execute(schema[table]["ddl"])
        cursor.execute("CREATE TABLE admins(id INT PRIMARY KEY,full_name VARCHAR(255))")
        cursor.execute(
            "CREATE TABLE auth_users(id INT AUTO_INCREMENT PRIMARY KEY,username VARCHAR(50) UNIQUE,password VARCHAR(255),role VARCHAR(20),ref_id INT)"
        )
        cursor.execute(
            "INSERT INTO directions(id,name) VALUES(6,'Локальная проверка экзаменов')"
        )
        cursor.execute(
            "INSERT INTO students(id,full_name,class,tg_name) VALUES(2081,'Николай Прибыш — локальный тест',10,'')"
        )
        cursor.execute("INSERT INTO admins VALUES(1,'Локальный администратор')")
        password = "LocalExamOnly-2026"
        identities = [("admin", 1, "smoke-admin"), ("student", 2081, "smoke-student")]
        for number in range(1, 7):
            cursor.execute(
                "INSERT INTO examinators(id,full_name) VALUES(%s,%s)",
                (number, "Тестовый экзаменатор " + str(number)),
            )
            identities.append(("examinator", number, "smoke-examiner" + str(number)))
        for role, ident, login in identities:
            cursor.execute(
                "INSERT INTO auth_users(username,password,role,ref_id) VALUES(%s,%s,%s,%s)",
                (login, generate_password_hash(password), role, ident),
            )
        connection.commit()
        run_migrations(connection, "all", True, {})
        mysql_pool._pool = mysql.connector.pooling.MySQLConnectionPool(
            pool_name="exam_smoke",
            pool_size=16,
            pool_reset_session=True,
            unix_socket=args.socket,
            user="root",
            password="",
            database=database,
            autocommit=False,
        )
        app = Flask(__name__)
        app.config.update(
            SECRET_KEY=uuid.uuid4().hex,
            JWT_SECRET_KEY=uuid.uuid4().hex,
            EXAMS_V2_ENABLED=True,
            CLASSIC_EXAM_CREATION_ENABLED=True,
            CLASSIC_EXAM_COMMANDS_ENABLED=True,
            STUDENT_EXAM_RESULTS_V2_ENABLED=True,
            RATING_EXAMS_V2_ENABLED=True,
        )
        CORS(
            app,
            origins=["http://localhost:3007", "http://127.0.0.1:3007"],
            supports_credentials=True,
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "X-Exam-Confirmation",
            ],
            expose_headers=["X-Correlation-ID"],
        )
        app.before_request(enforce_admin_permissions)
        for blueprint in (
            auth_bp,
            directions_bp,
            exams_v2_bp,
            examiner_exams_bp,
            student_exams_bp,
            exam_imports_bp,
        ):
            app.register_blueprint(blueprint)
        with app.app_context():
            token = generate_token(
                {"role": "admin", "id": 1, "full_name": "Локальный администратор"}
            )

        def call(method, path, body):
            with app.test_client() as client:
                response = client.open(
                    path,
                    method=method,
                    json=body,
                    headers={
                        "Authorization": "Bearer " + token,
                        "Idempotency-Key": str(uuid.uuid4()),
                    },
                )
                if response.status_code >= 400:
                    raise RuntimeError("Local seed failed: " + str(response.json))
                return response.json["data"]

        exam = call("POST", "/api/exams", {"examType": "classic", "directionId": 6})[
            "exam"
        ]
        base = "/api/exams/" + str(exam["id"]) + "/classic"
        current = datetime.now(timezone.utc)
        config = call(
            "PATCH",
            base + "/config",
            {
                "expectedConfigVersion": 1,
                "startAt": (current - timedelta(days=1)).isoformat(),
                "endAt": (current + timedelta(days=1)).isoformat(),
                "fractionalMode": "round_up",
            },
        )["config"]
        part = call(
            "POST",
            base + "/parts",
            {"code": "A", "questionWeight": 5, "questionCount": 1},
        )["part"]
        for number in range(1, 5):
            call(
                "POST",
                base + "/questions",
                {
                    "partId": part["id"],
                    "questionText": "Тестовый вопрос " + str(number),
                    "answerText": "Эталонный ответ " + str(number),
                },
            )
        config = call("GET", base + "/config", None)["config"]
        call(
            "PUT",
            base + "/scoring",
            {
                "expectedConfigVersion": config["configVersion"],
                "thresholds": [{"grade": i, "minScore": i} for i in range(6)],
                "fractionalMode": "round_up",
            },
        )
        commission = call("POST", base + "/commissions", {"examinatorIds": [1]})[
            "commission"
        ]
        call(
            "POST",
            base + "/assignments",
            {
                "studentId": 2081,
                "commissionId": commission["id"],
                "replacementLimit": 1,
            },
        )
        outside = call(
            "POST",
            "/api/exams",
            {
                "examType": "outside_lms",
                "directionId": 6,
                "date": current.date().isoformat(),
            },
        )["exam"]
        call(
            "POST",
            "/api/exams/" + str(outside["id"]) + "/outside-lms/results",
            {
                "studentId": 2081,
                "points": 7.25,
                "grade": 4,
                "examinator": "Локальная проверка",
            },
        )
        print(
            json.dumps(
                {
                    "localOnly": True,
                    "database": database,
                    "classicExamId": exam["id"],
                    "outsideExamId": outside["id"],
                    "logins": ["smoke-admin", "smoke-examiner1", "smoke-student"],
                    "syntheticPassword": password,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        app.run(
            host="127.0.0.1",
            port=args.port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
    finally:
        connection.rollback()
        assert database.startswith("cpm_exam_smoke_")
        cursor.execute("DROP DATABASE " + identifier(database))
        cursor.close()
        connection.close()


if __name__ == "__main__":
    main()
