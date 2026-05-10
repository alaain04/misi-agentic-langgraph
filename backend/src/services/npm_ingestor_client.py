"""HTTP client for the entity-worker ingestor service."""

import asyncio
import logging

import httpx

from src.utils.config import settings

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 5.0
_REQUEST_TIMEOUT = 10.0


async def ingest(entity_type: str, items: list[str]) -> str:
    """Submit items for ingestion. Returns job_id."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.npm_worker_url}/ingest",
            json={"entity_type": entity_type, "items": items},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["job_id"]


async def wait(job_id: str, timeout: float = 300.0) -> None:
    """Poll /status until job is done or timeout expires."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with httpx.AsyncClient() as client:
        while loop.time() < deadline:
            resp = await client.get(
                f"{settings.npm_worker_url}/status/{job_id}",
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            if resp.json()["status"] == "done":
                return
            await asyncio.sleep(_POLL_INTERVAL)
    logger.warning("ingestor: timeout waiting for job %s", job_id)
