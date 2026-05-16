"""NATS JetStream pull-consumer — dispatches to fetcher by entity type."""

import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
from nats.aio.msg import Msg
from nats.js.api import ConsumerConfig
from nats.js.errors import FetchTimeoutError

from src import fetchers, jobs
from src.config import settings
from src.db import get_db
from src.fetchers.errors import PermanentFetchError, RateLimitError, TransientFetchError
from src.nats_client import STREAM_NAME, get_js
from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


async def _save(collection: str, name: str, doc: dict) -> None:
    await get_db()[collection].replace_one(
        {"name": name},
        {"name": name, "fetched_at": datetime.now(UTC), **doc},
        upsert=True,
    )


def _backoff(num_delivered: int) -> float:
    delay = settings.nats_transient_backoff_base * (2 ** (num_delivered - 1))
    return min(delay, settings.nats_transient_backoff_cap)


async def _process(
    msg: Msg,
    client: httpx.AsyncClient,
    rate_limiter: RateLimiter,
) -> None:
    data = json.loads(msg.data)
    job_id: str = data["job_id"]
    entity_type: str = data["entity_type"]
    name: str = data["name"]
    num_delivered: int = msg.metadata.num_delivered

    try:
        entry = fetchers.get(entity_type)
        doc = await entry.fetch_fn(client, name, rate_limiter, settings.max_retries)
        await _save(entry.collection, name, doc)
        await jobs.record_success(job_id)
        await msg.ack()

    except RateLimitError as exc:
        logger.warning("consumer: rate limited %s/%s, requeue in %.0fs", entity_type, name, exc.delay)
        await msg.nak(delay=exc.delay)

    except TransientFetchError as exc:
        if num_delivered >= settings.nats_max_deliver:
            logger.error("consumer: exhausted retries %s/%s: %s", entity_type, name, exc)
            await msg.term()
            await jobs.record_failure(job_id)
        else:
            delay = _backoff(num_delivered)
            logger.warning("consumer: transient error %s/%s, requeue in %.0fs", entity_type, name, delay)
            await msg.nak(delay=delay)

    except PermanentFetchError as exc:
        logger.error("consumer: permanent error %s/%s: %s", entity_type, name, exc)
        await msg.term()
        await jobs.record_failure(job_id)

    except Exception as exc:
        logger.error("consumer: unexpected error %s/%s: %s", entity_type, name, exc)
        await msg.term()
        await jobs.record_failure(job_id)


async def _worker(
    sub,
    client: httpx.AsyncClient,
    rate_limiter: RateLimiter,
) -> None:
    while True:
        try:
            msgs = await sub.fetch(1, timeout=1.0)
            for msg in msgs:
                await _process(msg, client, rate_limiter)
        except asyncio.CancelledError:
            break
        except FetchTimeoutError:
            pass
        except Exception:
            logger.warning("worker: fetch error", exc_info=True)
            await asyncio.sleep(0.1)


async def run_consumer(rate_limiter: RateLimiter) -> None:
    js = get_js()
    sub = await js.pull_subscribe(
        "entity.fetch.*",
        durable="entity-worker",
        stream=STREAM_NAME,
        config=ConsumerConfig(max_deliver=settings.nats_max_deliver),
    )
    async with httpx.AsyncClient() as client:
        tasks = [
            asyncio.create_task(_worker(sub, client, rate_limiter))
            for _ in range(settings.worker_concurrency)
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
