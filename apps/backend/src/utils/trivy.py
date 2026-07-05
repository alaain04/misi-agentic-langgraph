"""Shared Trivy runner via ContainerRunPort."""

import logging

from src.domain.ports.container_run_port import ContainerRunPort

logger = logging.getLogger(__name__)

_TRIVY_IMAGE = "aquasec/trivy:latest"
_TRIVY_VOLUME_TEMPLATE = "{repo_path}:/repo"


async def run_trivy(
    container: ContainerRunPort, repo_path: str, *trivy_args: str
) -> tuple[dict, str]:
    """Run a Trivy scan via ContainerRunPort. Returns (parsed_json, stderr)."""
    import json

    volume = _TRIVY_VOLUME_TEMPLATE.format(repo_path=repo_path)
    command = "trivy fs --quiet --cache-dir /tmp/trivy-cache " + " ".join(trivy_args) + " /repo"
    returncode, stdout, stderr = await container.run(
        _TRIVY_IMAGE, command, volume
    )

    if returncode != 0:
        raise RuntimeError(f"Trivy exited {returncode}: {stderr.strip()[:500]}")

    raw = stdout.strip()
    if not raw:
        return {}, stderr

    return json.loads(raw), stderr
