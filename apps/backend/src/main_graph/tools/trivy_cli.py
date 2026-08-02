"""Trivy tools executed inside a sandboxed container: SBOM/dependency-graph
generation, vulnerability scanning, and license scanning.

Each function is its own separate `trivy fs` invocation rather than one
combined `--scanners vuln,license` call — VulnerabilityAgent and LicenseAgent
are dispatched independently based on relevance to the user's concern (see
deepagent/coverage.WHOLE_TREE_AGENT_TYPES), so bundling their data sources
would force every job to pay for both scanners regardless of which agent(s)
actually ran. All three share one persistent cache_volume for Trivy's
vulnerability DB (~100MB) so it downloads once, not on every ephemeral
`docker run --rm` invocation — see docs/superpowers/plans/
2026-07-31-trivy-adoption.md.
"""

from __future__ import annotations

import json
import logging
import os

from src.domain.ports.container_run_port import ContainerRunPort
from src.utils.config import settings

logger = logging.getLogger(__name__)

_CACHE_VOLUME_TARGET = "/root/.cache/trivy"


def _safe_json(text: str) -> dict:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


async def _run_trivy(
    args: list[str], repo_path: str, container: ContainerRunPort
) -> tuple[int, str, str]:
    os.makedirs(settings.trivy_cache_dir, exist_ok=True)
    command = "trivy " + " ".join(args) + " /workspace"
    volume = f"{repo_path}:/workspace"
    cache_volume = f"{os.path.abspath(settings.trivy_cache_dir)}:{_CACHE_VOLUME_TARGET}"
    return await container.run(
        image=settings.trivy_image,
        command=command,
        volume=volume,
        run_as_root=True,
        cache_volume=cache_volume,
    )


async def _scan(
    args: list[str],
    repo_path: str,
    container: ContainerRunPort,
    success_key: str,
    scan_name: str,
) -> dict:
    try:
        rc, stdout, stderr = await _run_trivy(args, repo_path, container)
    except Exception as exc:
        logger.warning("%s failed: %s", scan_name, exc)
        return {"error": str(exc)}

    result = _safe_json(stdout)
    if success_key not in result:
        detail = stderr.strip() or stdout.strip() or f"exit code {rc}"
        logger.warning("%s: no usable output (rc=%d): %s", scan_name, rc, detail[:300])
        return {"error": f"{scan_name} failed: {detail[:500]}"}
    return result


async def trivy_sbom_scan(repo_path: str, container: ContainerRunPort) -> dict:
    """Runs `trivy fs --format cyclonedx` (no vuln/license scanners — see
    module docstring). Returns the CycloneDX document: components +
    dependency-graph edges, no vulnerability or license data."""
    return await _scan(
        ["fs", "--format", "cyclonedx"],
        repo_path,
        container,
        success_key="bomFormat",
        scan_name="trivy_sbom_scan",
    )


async def trivy_vuln_scan(repo_path: str, container: ContainerRunPort) -> dict:
    """Runs `trivy fs --format json --scanners vuln`. Returns the raw Trivy
    vulnerability report (Results[].Vulnerabilities[])."""
    return await _scan(
        ["fs", "--format", "json", "--scanners", "vuln"],
        repo_path,
        container,
        success_key="SchemaVersion",
        scan_name="trivy_vuln_scan",
    )


async def trivy_license_scan(repo_path: str, container: ContainerRunPort) -> dict:
    """Runs `trivy fs --format json --scanners license`. Returns the raw
    Trivy license report (Results[].Licenses[])."""
    return await _scan(
        ["fs", "--format", "json", "--scanners", "license"],
        repo_path,
        container,
        success_key="SchemaVersion",
        scan_name="trivy_license_scan",
    )
