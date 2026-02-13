"""
Единый бэкенд CPM: авторизация, cpm-serv, cpm-exam, прокси.
"""
import logging
import time
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from .config import config
from .db.mysql_pool import init_mysql_pool
from .db.mongo import init_mongo
from .blueprints import (
    auth_bp,
    homework_bp,
    students_bp,
    groups_bp,
    attendance_bp,
    class_days_bp,
    users_bp,
    schedule_bp,
    zaps_bp,
    cards_bp,
    directions_bp,
    tests_bp,
    exams_bp,
    external_tests_bp,
    ratings_bp,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = config.SECRET_KEY
    app.config['ENV'] = config.ENV
    app.config['JWT_SECRET_KEY'] = config.JWT_SECRET_KEY
    app.config['JWT_ALGORITHM'] = config.JWT_ALGORITHM
    app.config['JWT_EXPIRATION_HOURS'] = config.JWT_EXPIRATION_HOURS

    from flask_cors import CORS
    CORS(app, resources={
        r"/*": {
            "origins": config.CORS_ORIGINS,
            "methods": ["GET", "POST", "OPTIONS", "PUT", "DELETE"],
            "allow_headers": ["Content-Type", "Authorization"],
            "supports_credentials": True,
            "expose_headers": ["Content-Type"]
        }
    })

    init_mysql_pool(config)
    init_mongo(config)

    @app.before_request
    def log_request():
        request.start_time = time.time()
        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', 'unknown'))
        logger.info(f"[CPM-BACK REQUEST] {request.method} {request.path} | IP: {client_ip}")
        if request.method in ('POST', 'PUT') and request.is_json:
            try:
                body = str(request.get_json())[:500]
                logger.info(f"[CPM-BACK BODY] {body}")
            except Exception:
                pass

    @app.after_request
    def log_response(response):
        duration = (time.time() - getattr(request, 'start_time', time.time())) * 1000
        logger.info(f"[CPM-BACK RESPONSE] {request.method} {request.path} | Status: {response.status_code} | {duration:.2f}ms")
        return response

    @app.errorhandler(Exception)
    def handle_exception(e):
        # Не превращаем штатные HTTP-ошибки (404/405/и т.д.) в 500.
        if isinstance(e, HTTPException):
            return jsonify({"error": e.name, "message": e.description}), e.code
        logger.error(f"[CPM-BACK ERROR] {request.method} {request.path} | {str(e)}", exc_info=True)
        return jsonify({"error": "Internal server error", "message": str(e)}), 500

    @app.route('/')
    def index():
        return jsonify({"service": "cpm-back", "status": "ok"})

    app.register_blueprint(auth_bp)
    app.register_blueprint(homework_bp)
    app.register_blueprint(students_bp)
    app.register_blueprint(groups_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(class_days_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(schedule_bp)
    app.register_blueprint(zaps_bp)
    app.register_blueprint(cards_bp)
    app.register_blueprint(directions_bp)
    app.register_blueprint(tests_bp)
    app.register_blueprint(exams_bp)
    app.register_blueprint(external_tests_bp)
    app.register_blueprint(ratings_bp)

    return app
