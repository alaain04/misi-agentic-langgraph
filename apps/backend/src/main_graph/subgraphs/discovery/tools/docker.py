"""Docker tools: module-level tool (legacy) and DI factory (current)."""

import asyncio
import json

from langchain_core.tools import tool

from src.domain.ports.container_run_port import ContainerRunPort


@tool
async def run_docker_command(image: str, volume: str, command: str) -> str:
    """Run a shell command in a Docker container with the workspace mounted.

    Args:
        image: Docker image, e.g. "node:25-alpine"
        volume: Docker volume spec, e.g. "/host/path:/container/path"
        command: Shell command to run inside the container

    Returns JSON with keys: returncode (int), stdout (str), stderr (str).
    """
    host_path = volume.split(":")[0] if ":" in volume else volume
    proc = await asyncio.create_subprocess_exec(
        "docker", "run", "--rm", "-v", volume, image, "sh", "-c", command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=host_path,
    )
    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=120)
    return json.dumps({
        "returncode": proc.returncode,
        "stdout": stdout_b.decode() if stdout_b else "",
        "stderr": stderr_b.decode() if stderr_b else "",
    })


def make_docker_tool(container: ContainerRunPort):
    """Return a LangChain @tool that runs Docker commands via container port."""

    @tool
    async def run_docker_command(image: str, volume: str, command: str) -> str:
        """Run a shell command in a Docker container with the workspace mounted.

        Args:
            image: Docker image, e.g. "node:25-alpine"
            volume: Docker volume spec, e.g. "/host/path:/container/path"
            command: Shell command to run inside the container

        Returns JSON with keys: returncode (int), stdout (str), stderr (str).
        """
        returncode, stdout, stderr = await container.run(image, command, volume)
        return json.dumps(
            {"returncode": returncode, "stdout": stdout, "stderr": stderr}
        )

    return run_docker_command
