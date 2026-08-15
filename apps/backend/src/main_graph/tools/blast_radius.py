from __future__ import annotations

import json
import shlex

from langchain_core.tools import tool

from src.domain.ports.container_run_port import ContainerRunPort
from src.utils.config import settings


async def compute_blast_radius(
    package_name: str,
    repo_path: str,
    container: ContainerRunPort,
) -> dict:
    """The import/usage graph blast radius of a dependency."""
    result = {
        "package_name": package_name,
        "available": False,
        "affected_file_count": 0,
        "affected_files": [],
        "node_count": 0,
    }
    try:
        command = f"codegraph impact {shlex.quote(package_name)} --json -p /workspace"
        rc, stdout, stderr = await container.run(
            image=settings.codegraph_docker_image,
            command=command,
            volume=f"{repo_path}:/workspace",
            run_as_root=True,
        )
        if rc != 0:
            result["error"] = stderr[:300] or f"exit {rc}"
            return result

        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        result["error"] = "unparseable codegraph output"
        return result

    affected_files = data.get("affected", [])

    result["available"] = True
    result["affected_file_count"] = len(affected_files)
    result["affected_files"] = affected_files
    result["node_count"] = data.get("nodeCount", 0)

    return result


def make_blast_radius_tool(repo_path: str, container: ContainerRunPort, image: str):
    @tool
    async def blast_radius(package_name: str, depth: int = 3) -> dict:
        """Compute the real import/usage graph blast radius of a risky
        dependency: which files actually import it, how many, and whether
        that usage is isolated to tests/scripts or reaches production code."""
        return await compute_blast_radius(
            package_name, repo_path, container, image, depth
        )

    return blast_radius
