import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.config import settings
from src.consumer import run_consumer
from src.nats_client import close as nats_close
from src.nats_client import connect as nats_connect
from src.rate_limiter import TokenBucket
from src.routers import ingest

logger = logging.getLogger(__name__)

_consumer_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _consumer_task
    await nats_connect()
    buckets = {
        "npm": TokenBucket(settings.npm_rate_limit_rps),
        "github": TokenBucket(settings.github_rate_limit_rps),
    }
    _consumer_task = asyncio.create_task(run_consumer(buckets))
    logger.info("entity-worker started")
    yield
    if _consumer_task:
        _consumer_task.cancel()
    await nats_close()
    logger.info("entity-worker stopped")


app = FastAPI(title="entity-worker", lifespan=lifespan)
app.include_router(ingest.router)
