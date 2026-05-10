"""NATS JetStream pull-consumer — dispatches to fetcher by entity type."""

import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
from nats.aio.msg import Msg
from nats.js.errors import FetchTimeoutError

from src import fetchers, jobs
from src.config import settings
from src.db import get_db
from src.nats_client import STREAM_NAME, get_js
from src.rate_limiter import TokenBucket

logger = logging.getLogger(__name__)


async def _save(collection: str, name: str, doc: dict) -> None:
    await get_db()[collection].replace_one(
        {"name": name},
        {"name": name, "fetched_at": datetime.now(UTC), **doc},
        upsert=True,
    )


async def _process(
    msg: Msg,
    client: httpx.AsyncClient,
    buckets: dict[str, TokenBucket],
) -> None:
    data = json.loads(msg.data)
    job_id: str = data["job_id"]
    entity_type: str = data["entity_type"]
    name: str = data["name"]
    try:
        fetch_fn, collection = fetchers.get(entity_type)
        bucket = buckets[entity_type]
        doc = await fetch_fn(client, name, bucket, settings.max_retries)
        await _save(collection, name, doc)
        await jobs.record_success(job_id)
        await msg.ack()
    except Exception as exc:
        logger.error("consumer: failed %s/%s: %s", entity_type, name, exc)
        await jobs.record_failure(job_id)
        await msg.ack()


async def _worker(
    sub,
    client: httpx.AsyncClient,
    buckets: dict[str, TokenBucket],
) -> None:
    while True:
        try:
            msgs = await sub.fetch(1, timeout=1.0)
            for msg in msgs:
                await _process(msg, client, buckets)
        except asyncio.CancelledError:
            break
        except FetchTimeoutError:
            pass  # queue empty, just loop
        except Exception:
            logger.warning("worker: fetch error", exc_info=True)
            await asyncio.sleep(0.1)


async def run_consumer(buckets: dict[str, TokenBucket]) -> None:
    missing = set(fetchers._REGISTRY.keys()) - set(buckets.keys())
    if missing:
        raise ValueError(f"run_consumer: missing buckets for entity types: {missing}")
    js = get_js()
    sub = await js.pull_subscribe(
        "entity.fetch.*", durable="entity-worker", stream=STREAM_NAME
    )
    async with httpx.AsyncClient() as client:
        tasks = [
            asyncio.create_task(_worker(sub, client, buckets))
            for _ in range(settings.worker_concurrency)
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
