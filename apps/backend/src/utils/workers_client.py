"""Workers API client — submit ingest jobs and poll until complete."""

import asyncio
import logging

import httpx

from src.utils.config import settings

_log = logging.getLogger(__name__)


async def ingest_and_wait(
    entity_types: list[str],
    items: list[str],
    max_wait: float = 30.0,
) -> dict[str, str]:
    """Submit an ingest job and poll until all entity_types complete.

    Returns a mapping of entity_type → job_id for all completed jobs.
    Raises RuntimeError if any job fails or the total wait exceeds max_wait.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.workers_url}/ingest",
            json={"entity_types": entity_types, "items": items},
            timeout=10.0,
        )
        resp.raise_for_status()
        job_ids: dict[str, str] = resp.json()["job_ids"]

    await _poll_all(job_ids, max_wait)
    return job_ids


async def _poll_all(job_ids: dict[str, str], max_wait: float) -> None:
    pending = dict(job_ids)
    elapsed = 0.0
    delay = 1.0

    while pending and elapsed < max_wait:
        await asyncio.sleep(delay)
        elapsed += delay
        delay = min(delay * 1.5, 5.0)

        done: list[str] = []
        async with httpx.AsyncClient() as client:
            for entity_type, job_id in pending.items():
                resp = await client.get(
                    f"{settings.workers_url}/status/{job_id}", timeout=5.0
                )
                resp.raise_for_status()
                status = resp.json()["status"]
                if status == "done":
                    done.append(entity_type)
                elif status == "failed":
                    raise RuntimeError(
                        f"Workers job {job_id} ({entity_type}) failed"
                    )

        for et in done:
            del pending[et]

    if pending:
        raise RuntimeError(
            f"Workers jobs timed out after {max_wait}s: {list(pending.keys())}"
        )
