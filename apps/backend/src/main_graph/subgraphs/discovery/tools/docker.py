"""Factory for a LangChain Docker tool backed by ContainerRunPort."""

import json

from langchain_core.tools import tool

from src.domain.ports.container_run_port import ContainerRunPort


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
