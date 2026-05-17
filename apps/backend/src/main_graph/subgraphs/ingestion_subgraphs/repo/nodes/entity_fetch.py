"""EntityFetcher — generic GitHub entity fetcher with retry and backoff."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

_log = logging.getLogger(__name__)

_FATAL_PATTERNS = ["404", "403", "not found", "access denied", "forbidden"]
_RETRY_DELAYS = [1, 2, 4]


def _is_fatal(exc: Exception, patterns: list[str]) -> bool:
    msg = str(exc).lower()
    return any(p in msg for p in patterns)


def _lookback_since(lookback_days: int) -> str:
    since = datetime.now(UTC) - timedelta(days=lookback_days)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")


class EntityFetcher:
    """Generic fetch parametrized by entity type."""

    ENTITY_TYPES = frozenset({"commits", "issues", "releases", "vulnerabilities"})

    def __init__(self, entity_type: str, client: Any) -> None:
        if entity_type not in self.ENTITY_TYPES:
            raise ValueError(f"Unknown entity_type: {entity_type!r}")
        self._entity_type = entity_type
        self._client = client

    async def fetch(
        self,
        owner: str,
        repo: str,
        since: str,
        until: str | None = None,
        fatal_patterns: list[str] | None = None,
    ) -> list[dict]:
        patterns = fatal_patterns if fatal_patterns is not None else _FATAL_PATTERNS
        last_exc: Exception | None = None

        for attempt, delay in enumerate(_RETRY_DELAYS):
            try:
                return await self._dispatch(owner, repo, since, until)
            except Exception as exc:
                last_exc = exc
                if _is_fatal(exc, patterns):
                    raise
                if attempt < len(_RETRY_DELAYS) - 1:
                    await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    async def _dispatch(
        self, owner: str, repo: str, since: str, until: str | None
    ) -> list[dict]:
        c = self._client
        if self._entity_type == "commits":
            return await c.list_commits(owner, repo, since, until)
        if self._entity_type == "issues":
            return await c.list_issues(owner, repo, since)
        if self._entity_type == "releases":
            return await c.list_releases(owner, repo, since)
        return await c.list_vulnerability_advisories(owner, repo, since)
