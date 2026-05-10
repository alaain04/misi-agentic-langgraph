from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from src.config import settings

_client: AsyncMongoClient | None = None


def get_client() -> AsyncMongoClient:
    global _client
    if _client is None:
        _client = AsyncMongoClient(settings.mongodb_uri)
    return _client


def get_db() -> AsyncDatabase:
    return get_client().get_database()
