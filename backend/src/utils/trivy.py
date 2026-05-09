"""Shared Trivy Docker runner."""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

_TRIVY_IMAGE = "aquasec/trivy:latest"
_TRIVY_TIMEOUT = 300


async def run_trivy(repo_path: str, *trivy_args: str) -> tuple[dict, str]:
    """Run a Trivy Docker command and return (parsed_json, raw_stderr)."""
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{repo_path}:/repo",
        _TRIVY_IMAGE,
        "fs", "--quiet",
        *trivy_args,
        "/repo",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_TRIVY_TIMEOUT
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"Trivy timed out after {_TRIVY_TIMEOUT}s")

    stderr_str = stderr.decode(errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"Trivy exited {proc.returncode}: {stderr_str.strip()[:500]}")

    raw = stdout.decode(errors="replace").strip()
    if not raw:
        return {}, stderr_str

    return json.loads(raw), stderr_str
