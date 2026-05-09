"""Node: generate_sbom — run Trivy CycloneDX scan and persist the SBOM."""

import logging
from pathlib import Path

from src.main_graph.subgraphs.discovery.dao import sbom_dao
from src.main_graph.subgraphs.discovery.models import SbomEntry
from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.utils.trivy import run_trivy

logger = logging.getLogger(__name__)

_MANIFESTS = ("package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json")


def _detect_manifest_files(repo_path: str) -> list[str]:
    root = Path(repo_path)
    return [name for name in _MANIFESTS if (root / name).exists()]


async def generate_sbom(state: DiscoveryState) -> dict:
    repo_path = state.get("repo_path", "")

    if not repo_path:
        logger.error("generate_sbom: no repo_path in state")
        entry = SbomEntry(repo_url=state.get("repo_url", ""), scan_error="repo_path not available")
        result_id = await sbom_dao.save(entry)
        return {
            "sbom_cyclonedx": {},
            "sbom_result_id": result_id,
            "manifest_files": [],
            "sbom_error": "repo_path not available",
        }

    sbom_data: dict = {}
    sbom_error: str | None = None

    try:
        logger.info("generate_sbom: running Trivy CycloneDX scan on %s", repo_path)
        sbom_data, _ = await run_trivy(repo_path, "--format", "cyclonedx")
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_sbom: scan failed")
        sbom_error = str(exc)

    manifest_files = _detect_manifest_files(repo_path)

    entry = SbomEntry(
        repo_url=state.get("repo_url", ""),
        sbom_cyclonedx=sbom_data,
        scan_error=sbom_error,
    )
    result_id = await sbom_dao.save(entry)
    logger.info("generate_sbom: saved — result_id=%s error=%s", result_id, sbom_error)

    result: dict = {
        "sbom_cyclonedx": sbom_data,
        "sbom_result_id": result_id,
        "manifest_files": manifest_files,
    }
    if sbom_error:
        result["sbom_error"] = sbom_error
    return result
