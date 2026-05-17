import asyncio
import json
import os

from langchain_core.tools import tool

_DOCKER_TIMEOUT = 300


@tool
async def run_docker_command(
    image: str,
    volume: str,
    command: str,
) -> str:
    """Run a shell command in a Docker container with the workspace mounted.

    Args:
        image: Docker image, e.g. "node:25-alpine"
        volume: Docker volume spec, e.g. "/host/path:/container/path"
        command: Shell command to run, e.g.
            "cd /workspace && npm -g install pnpm && pnpm install"

    Returns JSON with keys: returncode (int), stdout (str), stderr (str).
    """
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{volume}",
        image,
        "sh",
        "-c",
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=_DOCKER_TIMEOUT
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return json.dumps(
            {
                "returncode": -1,
                "stdout": "",
                "stderr": f"timed out after {_DOCKER_TIMEOUT}s",
            }
        )
    return json.dumps(
        {
            "returncode": proc.returncode,
            "stdout": stdout_b.decode(errors="replace"),
            "stderr": stderr_b.decode(errors="replace")[:3000],
        }
    )
