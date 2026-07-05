"""npm subprocess tools: npm_list, npm_audit, npm_outdated."""
from __future__ import annotations

import asyncio
import json
import logging

from src.main_graph.tools.registry import register

logger = logging.getLogger(__name__)


async def _run_npm(args: list[str], cwd: str) -> tuple[str, str]:
    proc = await asyncio.create_subprocess_exec(
        "npm", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode(), stderr.decode()


def _safe_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


@register("npm_list", "Runs `npm list --json`; returns full dependency tree with installed versions")
async def npm_list(repo_path: str) -> dict:
    try:
        stdout, _ = await _run_npm(["list", "--json", "--all"], repo_path)
        return _safe_json(stdout)
    except Exception as exc:
        logger.warning("npm_list failed: %s", exc)
        return {"error": str(exc)}


@register("npm_audit", "Runs `npm audit --json`; returns vulnerabilities, severities, and affected packages")
async def npm_audit(repo_path: str) -> dict:
    try:
        stdout, _ = await _run_npm(["audit", "--json"], repo_path)
        return _safe_json(stdout)
    except Exception as exc:
        logger.warning("npm_audit failed: %s", exc)
        return {"error": str(exc)}


@register("npm_outdated", "Returns packages with newer versions available via `npm outdated --json`")
async def npm_outdated(repo_path: str) -> dict:
    try:
        stdout, _ = await _run_npm(["outdated", "--json"], repo_path)
        data = _safe_json(stdout)
        return {"outdated": data}
    except Exception as exc:
        logger.warning("npm_outdated failed: %s", exc)
        return {"error": str(exc)}
