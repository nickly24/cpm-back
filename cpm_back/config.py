"""
Конфигурация единого бэкенда CPM.
Переменные окружения переопределяют значения по умолчанию.
"""
import os


class Config:
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
        'mongodb://gen_user:I_OBNu~9oHF0(m@81.200.148.71:27017/default_db?authSource=admin&directConnection=true'
    )
    MONGODB_DB_NAME = os.environ.get('MONGODB_DB_NAME', 'default_db')

    # JWT (единая авторизация)
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'dev-secret-key-cpm-lms-2025-change-in-production')
    JWT_ALGORITHM = 'HS256'
    JWT_EXPIRATION_HOURS = 24

    # Пул MySQL
    MYSQL_POOL_SIZE = int(os.environ.get('MYSQL_POOL_SIZE', '25'))

    # CORS
    CORS_ORIGINS = [
        'https://cpm-lms.ru',
        'http://localhost:3000',
        'http://127.0.0.1:3000',
    ]


config = Config()
