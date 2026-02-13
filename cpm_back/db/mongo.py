"""
Единый клиент MongoDB для тестов, сессий, рейтингов.
"""
from pymongo import MongoClient

_client = None
_config = None


def init_mongo(config):
    global _client, _config
    _config = config
    _client = MongoClient(
        config.MONGODB_URI,
        maxPoolSize=50,
    )
    return _client


def get_mongo_client():
    if _client is None:
        raise RuntimeError("MongoDB client not initialized. Call init_mongo(config) in create_app.")
    return _client


def get_mongo_db():
    if _config is None:
        raise RuntimeError("MongoDB not initialized.")
    return _client[_config.MONGODB_DB_NAME]
