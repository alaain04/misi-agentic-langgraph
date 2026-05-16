import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src import fetchers
from src.config import settings
from src.consumer import run_consumer
from src.nats_client import close as nats_close
from src.nats_client import connect as nats_connect
from src.rate_limiter import RateLimiter
from src.redis_client import close as redis_close
from src.redis_client import connect as redis_connect
from src.routers import ingest

logger = logging.getLogger(__name__)

_consumer_task: asyncio.Task | None = None

_RATE_CONFIGS: dict[str, list[tuple[int, int]]] = {
    "npm": settings.npm_rate_windows,
    "github": settings.github_rate_windows,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _consumer_task
    await nats_connect()
    await redis_connect()

    missing = fetchers.rate_groups() - set(_RATE_CONFIGS)
    if missing:
        raise ValueError(f"No rate config for groups: {missing}")

    windows = {group: _RATE_CONFIGS[group] for group in fetchers.rate_groups()}
    rate_limiter = RateLimiter(windows)

    _consumer_task = asyncio.create_task(run_consumer(rate_limiter))
    logger.info("entity-worker started")
    yield

    if _consumer_task:
        _consumer_task.cancel()
        await asyncio.gather(_consumer_task, return_exceptions=True)
    await nats_close()
    await redis_close()
    logger.info("entity-worker stopped")


app = FastAPI(title="entity-worker", lifespan=lifespan)
app.include_router(ingest.router)
