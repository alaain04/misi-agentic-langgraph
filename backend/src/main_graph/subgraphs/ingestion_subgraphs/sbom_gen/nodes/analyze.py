"""Trivy scan node — runs Trivy via Docker and saves SBOM + scan results.

Mounts the shared cloned repo (repo_path from discovery), runs two scans:
  1. fs --format json --scanners vuln,license  → vulnerability + license data
  2. fs --format cyclonedx                     → SBOM

Deletes the cloned temp directory after both scans complete.
"""

import asyncio
import json
import logging
import shutil

from src.main_graph.subgraphs.ingestion_subgraphs.sbom_gen.dao import sbom_gen_dao
from src.main_graph.subgraphs.ingestion_subgraphs.sbom_gen.models import (
    SbomGenEntry,
    TrivyLicenseFinding,
    TrivyVulnerability,
)
from src.main_graph.subgraphs.ingestion_subgraphs.sbom_gen.state import SbomGenState

logger = logging.getLogger(__name__)

_TRIVY_IMAGE = "aquasec/trivy:latest"
_TRIVY_TIMEOUT = 300  # seconds — image pull + scan


async def _run_trivy(repo_path: str, *trivy_args: str) -> tuple[dict, str]:
    """Run a trivy Docker command and return (parsed_json, raw_stderr)."""
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{repo_path}:/repo",
        _TRIVY_IMAGE,
        "fs",
        "--quiet",
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
        raise RuntimeError(f"Trivy timed out after {_TRIVY_TIMEOUT}s")

    stderr_str = stderr.decode(errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"Trivy exited {proc.returncode}: {stderr_str.strip()[:500]}")

    raw = stdout.decode(errors="replace").strip()
    if not raw:
        return {}, stderr_str

    return json.loads(raw), stderr_str


def _parse_vulnerabilities(scan: dict) -> list[TrivyVulnerability]:
    findings: list[TrivyVulnerability] = []
    for result in scan.get("Results", []):
        for v in result.get("Vulnerabilities") or []:
            findings.append(
                TrivyVulnerability(
                    vulnerability_id=v.get("VulnerabilityID", ""),
                    pkg_name=v.get("PkgName", ""),
                    installed_version=v.get("InstalledVersion", ""),
                    fixed_version=v.get("FixedVersion") or None,
                    severity=v.get("Severity", "UNKNOWN"),
                    description=v.get("Description") or None,
                    primary_url=v.get("PrimaryURL") or None,
                )
            )
    return findings


def _parse_licenses(scan: dict) -> list[TrivyLicenseFinding]:
    findings: list[TrivyLicenseFinding] = []
    for result in scan.get("Results", []):
        for lic in result.get("Licenses") or []:
            findings.append(
                TrivyLicenseFinding(
                    pkg_name=lic.get("PkgName", ""),
                    license_name=lic.get("Name", ""),
                    category=lic.get("Category", "unknown"),
                    severity=lic.get("Severity", "UNKNOWN"),
                    confidence=lic.get("Confidence"),
                )
            )
    return findings


async def analyze(state: SbomGenState) -> dict:
    repo_path = state.get("repo_path", "")

    if not repo_path:
        logger.error("sbom_gen: no repo_path in state — cannot run scan")
        entry = SbomGenEntry(scan_error="repo_path not available")
        result_id = await sbom_gen_dao.save(entry)
        return {"result_id": result_id}

    scan_data: dict = {}
    sbom_data: dict = {}
    scan_error: str | None = None

    try:
        logger.info("sbom_gen: running scans on %s", repo_path)
        (scan_data, _), (sbom_data, _) = await asyncio.gather(
            _run_trivy(repo_path, "--format", "json", "--scanners", "vuln,license"),
            _run_trivy(repo_path, "--format", "cyclonedx"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("sbom_gen: scan failed")
        scan_error = str(exc)
    finally:
        shutil.rmtree(repo_path, ignore_errors=True)
        logger.info("sbom_gen: deleted temp dir %s", repo_path)

    vulnerabilities = _parse_vulnerabilities(scan_data)
    licenses = _parse_licenses(scan_data)

    entry = SbomGenEntry(
        vulnerabilities=vulnerabilities,
        licenses=licenses,
        sbom_cyclonedx=sbom_data,
        raw_scan=scan_data,
        scan_error=scan_error,
    )
    result_id = await sbom_gen_dao.save(entry)
    logger.info(
        "sbom_gen: saved — vulns=%d licenses=%d result_id=%s",
        len(vulnerabilities),
        len(licenses),
        result_id,
    )
    return {"result_id": result_id}
