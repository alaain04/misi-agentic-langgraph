"""FastAPI dependency injection wiring — all adapters and services."""

from pymongo import AsyncMongoClient

from adapters.cache.redis_adapter import RedisCacheAdapter
from adapters.db.mongodb.mongo_entity_cache_adapter import MongoEntityCacheAdapter
from adapters.db.mongodb.mongo_job_repository import MongoJobRepository
from adapters.fetchers.github_fetcher_adapter import (
    GithubAdvisoriesFetcherAdapter,
    GithubIssuesFetcherAdapter,
    GithubReleasesFetcherAdapter,
)
from adapters.fetchers.npm_fetcher_adapter import NpmFetcherAdapter
from adapters.messaging.nats_adapter import NATSJetStreamAdapter
from adapters.rate_limit.redis_rate_limiter import RedisRateLimiter
from config.settings import settings
from domain.ports.fetcher_port import FetcherPort
from domain.ports.messaging_port import MessagingPort
from services.application_services.consumer_service import ConsumerService
from services.application_services.ingest_service import IngestService

# ---------------------------------------------------------------------------
# Infrastructure clients
# ---------------------------------------------------------------------------

_mongo_client = AsyncMongoClient(settings.MONGODB_URI)
_mongo_db = _mongo_client[settings.MONGODB_DB]

# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

_job_repo = MongoJobRepository(_mongo_db)
_entity_cache = MongoEntityCacheAdapter(_mongo_db)

_messaging = NATSJetStreamAdapter(
    nats_url=settings.NATS_URL,
    stream_name=settings.NATS_STREAM_NAME,
    subject_prefix=settings.NATS_SUBJECT_PREFIX,
)

_cache = RedisCacheAdapter(settings.REDIS_URL)

_rate_limiter = RedisRateLimiter(
    redis_url=settings.REDIS_URL,
    windows={
        "npm": settings.NPM_RATE_WINDOWS,
        "github": settings.GITHUB_RATE_WINDOWS,
    },
)

_fetcher_registry: dict[str, FetcherPort] = {
    "npm": NpmFetcherAdapter(_rate_limiter, settings.MAX_RETRIES),
    "github_issues": GithubIssuesFetcherAdapter(
        _rate_limiter,
        settings.GITHUB_TOKEN,
        settings.GITHUB_ISSUES_LOOKBACK_DAYS,
        settings.MAX_RETRIES,
    ),
    "github_releases": GithubReleasesFetcherAdapter(
        _rate_limiter,
        settings.GITHUB_TOKEN,
        settings.GITHUB_RELEASES_LOOKBACK_DAYS,
        settings.MAX_RETRIES,
    ),
    "github_advisories": GithubAdvisoriesFetcherAdapter(
        _rate_limiter,
        settings.GITHUB_TOKEN,
        settings.MAX_RETRIES,
    ),
}

# ---------------------------------------------------------------------------
# Application services
# ---------------------------------------------------------------------------

_ingest_service = IngestService(
    job_repo=_job_repo,
    messaging=_messaging,
    subject_prefix=settings.NATS_SUBJECT_PREFIX,
)

_consumer_service = ConsumerService(
    messaging=_messaging,
    fetcher_registry=_fetcher_registry,
    entity_cache=_entity_cache,
    job_repo=_job_repo,
    settings=settings,
)

# ---------------------------------------------------------------------------
# Dependency providers
# ---------------------------------------------------------------------------


def get_messaging() -> MessagingPort:
    return _messaging


def get_fetcher_registry() -> dict[str, FetcherPort]:
    return _fetcher_registry


def get_ingest_service() -> IngestService:
    return _ingest_service


def get_consumer_service() -> ConsumerService:
    return _consumer_service


async def shutdown() -> None:
    await _cache.close()
    _mongo_client.close()
