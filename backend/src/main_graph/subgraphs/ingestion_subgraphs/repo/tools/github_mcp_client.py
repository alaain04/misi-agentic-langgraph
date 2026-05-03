"""GitHubMCPClient — connects to the GitHub MCP server via Docker stdio."""

from __future__ import annotations

import json
from typing import Any


class GitHubMCPClient:
    """Async context manager connecting to the GitHub MCP server via Docker stdio."""

    def __init__(self, pat: str) -> None:
        self._pat = pat
        self._client = None
        self._tools: dict = {}

    async def __aenter__(self) -> GitHubMCPClient:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        self._client = MultiServerMCPClient(
            {
                "github": {
                    "command": "docker",
                    "args": [
                        "run",
                        "--rm",
                        "-i",
                        "-e",
                        f"GITHUB_PERSONAL_ACCESS_TOKEN={self._pat}",
                        "ghcr.io/github/github-mcp-server",
                    ],
                    "transport": "stdio",
                }
            }
        )
        tools = await self._client.get_tools()
        self._tools = {t.name: t for t in tools}
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    async def _call(self, tool_name: str, **kwargs: Any) -> list[dict]:
        tool = self._tools[tool_name]
        raw = await tool.ainvoke(kwargs)
        if isinstance(raw, str):
            return json.loads(raw)
        return raw if isinstance(raw, list) else []

    async def list_commits(
        self, owner: str, repo: str, since: str, until: str
    ) -> list[dict]:
        results: list[dict] = []
        page = 1
        while True:
            batch = await self._call(
                "list_commits",
                owner=owner,
                repo=repo,
                since=since,
                until=until,
                per_page=100,
                page=page,
            )
            if not batch:
                break
            results.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return results

    async def list_issues(self, owner: str, repo: str, since: str) -> list[dict]:
        results: list[dict] = []
        page = 1
        while True:
            batch = await self._call(
                "list_issues",
                owner=owner,
                repo=repo,
                since=since,
                state="all",
                per_page=100,
                page=page,
            )
            if not batch:
                break
            results.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return results

    async def list_releases(self, owner: str, repo: str, since: str) -> list[dict]:
        results: list[dict] = []
        page = 1
        while True:
            batch = await self._call(
                "list_releases",
                owner=owner,
                repo=repo,
                per_page=100,
                page=page,
            )
            if not batch:
                break
            in_window = [r for r in batch if r.get("published_at", "") >= since]
            results.extend(in_window)
            if batch[-1].get("published_at", since) < since:
                break
            if len(batch) < 100:
                break
            page += 1
        return results

    async def list_vulnerability_advisories(
        self, owner: str, repo: str, since: str
    ) -> list[dict]:
        results: list[dict] = []
        page = 1
        while True:
            batch = await self._call(
                "list_repository_advisories",
                owner=owner,
                repo=repo,
                per_page=100,
                page=page,
            )
            if not batch:
                break
            in_window = [a for a in batch if a.get("published_at", "") >= since]
            results.extend(in_window)
            if len(batch) < 100:
                break
            page += 1
        return results
