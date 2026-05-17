import asyncio
import json
import logging
from typing import Any

import httpx

from domain.models.errors import PermanentFetchError, RateLimitError, TransientFetchError
from domain.ports.entity_cache_port import EntityCachePort
from domain.ports.fetcher_port import FetcherPort
from domain.ports.job_repository_port import JobRepositoryPort
from domain.ports.messaging_port import MessagingPort

logger = logging.getLogger(__name__)


class ConsumerService:
    def __init__(
        self,
        messaging: MessagingPort,
        fetcher_registry: dict[str, FetcherPort],
        entity_cache: EntityCachePort,
        job_repo: JobRepositoryPort,
        settings: Any,
    ) -> None:
        self._messaging = messaging
        self._fetcher_registry = fetcher_registry
        self._entity_cache = entity_cache
        self._job_repo = job_repo
        self._settings = settings

    async def run(self) -> None:
        sub = await self._messaging.pull_subscribe(
            stream=self._settings.NATS_STREAM_NAME,
            subject=f"{self._settings.NATS_SUBJECT_PREFIX}.*",
            durable="entity-worker",
            max_deliver=self._settings.NATS_MAX_DELIVER,
        )
        async with httpx.AsyncClient() as client:
            tasks = [
                asyncio.create_task(self._worker(sub, client))
                for _ in range(self._settings.WORKER_CONCURRENCY)
            ]
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

    async def _worker(self, sub: Any, client: httpx.AsyncClient) -> None:
        while True:
            try:
                msgs = await self._messaging.pull_fetch(sub, 1, timeout=1.0)
                for msg in msgs:
                    await self._process(msg, client)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("worker: fetch error", exc_info=True)
                await asyncio.sleep(0.1)

    def _backoff(self, num_delivered: int) -> float:
        delay = self._settings.NATS_TRANSIENT_BACKOFF_BASE * (2 ** (num_delivered - 1))
        return min(delay, self._settings.NATS_TRANSIENT_BACKOFF_CAP)

    async def _process(self, msg: Any, client: httpx.AsyncClient) -> None:
        data = json.loads(msg.data)
        job_id: str = data["job_id"]
        entity_type: str = data["entity_type"]
        name: str = data["name"]
        num_delivered: int = msg.metadata.num_delivered

        try:
            fetcher = self._fetcher_registry[entity_type]
            doc = await fetcher.fetch(client, name)
            await self._entity_cache.save(fetcher.collection, name, doc)
            await self._job_repo.record_success(job_id)
            await self._messaging.ack(msg)

        except RateLimitError as exc:
            logger.warning(
                "consumer: rate limited %s/%s, requeue in %.0fs", entity_type, name, exc.delay
            )
            await self._messaging.nak(msg, delay=exc.delay)

        except TransientFetchError as exc:
            if num_delivered >= self._settings.NATS_MAX_DELIVER:
                logger.error("consumer: exhausted retries %s/%s: %s", entity_type, name, exc)
                await self._messaging.term(msg)
                await self._job_repo.record_failure(job_id)
            else:
                delay = self._backoff(num_delivered)
                logger.warning(
                    "consumer: transient %s/%s, requeue in %.0fs", entity_type, name, delay
                )
                await self._messaging.nak(msg, delay=delay)

        except PermanentFetchError as exc:
            logger.error("consumer: permanent error %s/%s: %s", entity_type, name, exc)
            await self._messaging.term(msg)
            await self._job_repo.record_failure(job_id)

        except Exception as exc:
            logger.error("consumer: unexpected error %s/%s: %s", entity_type, name, exc)
            await self._messaging.term(msg)
            await self._job_repo.record_failure(job_id)
