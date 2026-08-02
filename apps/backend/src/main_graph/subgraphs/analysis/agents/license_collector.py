"""Collects each dependency's raw license string via Trivy's license scanner.

Trivy reads the lockfile's own per-package license metadata uniformly across
npm/pnpm/yarn, so there is no per-package-manager fallback path needed (the
old implementation's npm-registry HTTP fallback for yarn/pnpm is gone —
Trivy covers all three from the lockfile alone). A scan failure degrades
every package to "UNKNOWN" rather than raising, matching how the rest of
this pipeline treats scan errors as low-confidence, not fatal.
"""

from __future__ import annotations

import logging

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.tools.trivy_cli import trivy_license_scan
from src.models.results import PrepResult

logger = logging.getLogger(__name__)


async def collect_licenses(
    prep: PrepResult, container: ContainerRunPort
) -> dict[str, str]:
    """Return {"name@version": raw_license_string} for every package in
    prep.dependency_graph["packages"]. Unresolved packages (including a
    total scan failure) map to "UNKNOWN" — never guessed."""
    packages = prep.dependency_graph.get("packages", {})
    if not packages:
        return {}

    output = await trivy_license_scan(repo_path=prep.repo_path, container=container)
    if "error" in output:
        logger.warning("license_collector: trivy scan failed: %s", output["error"])
        return dict.fromkeys(packages, "UNKNOWN")

    by_name: dict[str, str] = {}
    for result in output.get("Results") or []:
        for lic in result.get("Licenses") or []:
            name = lic.get("PkgName")
            license_id = lic.get("Name")
            if name and license_id:
                by_name.setdefault(name, license_id)

    return {key: by_name.get(key.rsplit("@", 1)[0], "UNKNOWN") for key in packages}
