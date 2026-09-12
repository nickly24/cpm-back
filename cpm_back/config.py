"""
Конфигурация единого бэкенда CPM.
Переменные окружения переопределяют значения по умолчанию.
"""
import os


class Config:
    # Rollout capabilities are independent: disabling creation must not hide
    # published results or discard an exam already in progress.
    EXAMS_V2_ENABLED = os.environ.get('EXAMS_V2_ENABLED', 'false').lower() == 'true'
    CLASSIC_EXAM_CREATION_ENABLED = os.environ.get('CLASSIC_EXAM_CREATION_ENABLED', 'false').lower() == 'true'
    CLASSIC_EXAM_COMMANDS_ENABLED = os.environ.get('CLASSIC_EXAM_COMMANDS_ENABLED', 'false').lower() == 'true'
    STUDENT_EXAM_RESULTS_V2_ENABLED = os.environ.get('STUDENT_EXAM_RESULTS_V2_ENABLED', 'false').lower() == 'true'
    RATING_EXAMS_V2_ENABLED = os.environ.get('RATING_EXAMS_V2_ENABLED', 'false').lower() == 'true'

    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY', 'cpm-back-secret-change-in-production')
    ENV = os.environ.get('FLASK_ENV', 'production')

    # MySQL (общая БД для serv + exam)
    MYSQL_HOST = os.environ.get('MYSQL_HOST', '147.45.138.77')
    MYSQL_PORT = int(os.environ.get('MYSQL_PORT', '3306'))
    MYSQL_USER = os.environ.get('MYSQL_USER', 'minishep')
    MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', 'qwerty!1')
    MYSQL_DATABASE = os.environ.get('MYSQL_DATABASE', 'minishep')

    # MongoDB (тесты, сессии, рейтинги)
    MONGODB_URI = os.environ.get(
        'MONGODB_URI',
        'mongodb://gen_user:%23oc9gu%3A%7D%7C_p4fu@109.73.202.73:27017/default_db?authSource=admin&directConnection=true',
    )
    MONGODB_DB_NAME = os.environ.get('MONGODB_DB_NAME', 'default_db')

    # JWT (единая авторизация)
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'dev-secret-key-cpm-lms-2025-change-in-production')
    JWT_ALGORITHM = 'HS256'
    JWT_EXPIRATION_HOURS = 24

    # Пул MySQL
    MYSQL_POOL_SIZE = int(os.environ.get('MYSQL_POOL_SIZE', '25'))

    # CORS — прод + любой localhost/127.0.0.1 (любой порт)
    CORS_ORIGINS = [
        'https://cpm-lms.ru',
        'http://localhost:3000',
        'http://127.0.0.1:3000',
        'http://localhost:3001',
        'http://127.0.0.1:3001',
        'http://localhost:3002',
        'http://127.0.0.1:3002',
    ]
    # Доп. origin через env: CORS_ORIGINS_EXTRA=http://localhost:5173,http://192.168.1.5:3000
    CORS_ORIGINS_EXTRA = os.environ.get('CORS_ORIGINS_EXTRA', '')


config = Config()
