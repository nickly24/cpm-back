"""
Пул подключений MySQL для единого бэкенда.
"""
import mysql.connector
from mysql.connector import pooling

# Конфиг подставляется при первом использовании (после create_app)
_pool = None
_config = None


def init_mysql_pool(config):
    global _pool, _config
    _config = config
    _pool = pooling.MySQLConnectionPool(
        pool_name="cpm_back_pool",
        pool_size=config.MYSQL_POOL_SIZE,
        pool_reset_session=True,
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        autocommit=False,
    )
    return _pool


def get_db_connection():
    if _pool is None:
        raise RuntimeError("MySQL pool not initialized. Call init_mysql_pool(config) in create_app.")
    return _pool.get_connection()


def close_db_connection(connection):
    if connection:
        try:
            if connection.is_connected():
                connection.rollback()
        except Exception:
            pass
        try:
            connection.close()
        except Exception:
            pass
