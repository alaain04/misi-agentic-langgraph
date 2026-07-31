from __future__ import annotations

import logging
from typing import cast

from src.db.input_cache import InputCacheDAO
from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.analysis.agents.base_agent import BaseAgent
from src.main_graph.subgraphs.analysis.agents.license_collector import collect_licenses
from src.main_graph.subgraphs.analysis.agents.license_data import (
    LICENSES,
    UNLICENSED_ID,
    resolve,
)
from src.main_graph.subgraphs.analysis.agents.license_rules import check_conflicts
from src.main_graph.tools.package_files import _load_pkg
from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import AgentDispatch, EvidenceBundle, PrepResult

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1, "info": 0}


class LicenseAgent(BaseAgent):
    """Deterministic agent: one rule computation over the whole tree, not an
    exploratory investigation — mirrors VulnerabilityAgent since legal-risk
    findings should not depend on an LLM's compatibility judgment.
    packages_to_focus is ignored: a single copyleft transitive dependency
    matters regardless of which packages the conductor asked about.
    """

    agent_type = "license_agent"
    description = (
        "Analyzes license compatibility across the ENTIRE dependency tree against "
        "the project's own license: rights conflicts, obligation gaps, and copyleft "
        "contagion. Covers all direct and transitive packages in a single run, so "
        "packages_to_focus is ignored. Use when the concern involves license "
        "compliance, copyleft, or legal risk."
    )
    system_prompt = ""  # unused: run() does not invoke the LLM

    def _agent_tools(self) -> list:
        return []

    async def run(
        self,
        dispatch: AgentDispatch,
        prep: PrepResult,
        container: ContainerRunPort | None = None,
        cache: InputCacheDAO | None = None,
    ) -> tuple[EvidenceBundle, list[str], int]:
        pkg = _load_pkg(prep.repo_path)
        project_license_raw = pkg.get("license")
        if isinstance(project_license_raw, dict):  # legacy {"type": "MIT"} shape
            project_license_raw = project_license_raw.get("type")
        project_license_str = project_license_raw or UNLICENSED_ID
        project_resolved = resolve(project_license_str)
        project_id, project_entry = (
            project_resolved
            if project_resolved is not None
            else (UNLICENSED_ID, LICENSES[UNLICENSED_ID])
        )

        licenses = await collect_licenses(prep, cast(ContainerRunPort, container))
        findings: list[FindingNote] = []
        for key, raw_license in licenses.items():
            dep_name = key.rsplit("@", 1)[0]
            if raw_license == "UNKNOWN":
                findings.append(
                    FindingNote(
                        dep_name=dep_name,
                        severity="info",
                        description=(
                            "No license could be resolved for this dependency "
                            "(checked lockfile and npm registry) — manual review "
                            "required."
                        ),
                        evidence=[
                            EvidenceRef(
                                tool="license_collector",
                                url=None,
                                log_snippet=f"package={key}",
                            )
                        ],
                    )
                )
                continue

            resolved = resolve(raw_license)
            if resolved is None:
                findings.append(
                    FindingNote(
                        dep_name=dep_name,
                        severity="info",
                        description=(
                            f"License expression '{raw_license}' is not in the "
                            f"curated license table — manual review required."
                        ),
                        evidence=[
                            EvidenceRef(
                                tool="license_collector",
                                url=None,
                                log_snippet=f"package={key} license={raw_license}",
                            )
                        ],
                    )
                )
                continue

            dep_id, dep_entry = resolved
            for conflict in check_conflicts(
                project_id, project_entry, dep_id, dep_entry
            ):
                findings.append(
                    FindingNote(
                        dep_name=dep_name,
                        severity=conflict.severity,
                        description=conflict.detail,
                        evidence=[
                            EvidenceRef(
                                tool="license_rules",
                                url=None,
                                log_snippet=(
                                    f"{conflict.rule}: project={project_id} "
                                    f"dep={dep_id}"
                                ),
                            )
                        ],
                    )
                )

        findings.sort(key=lambda f: _SEVERITY_RANK.get(f.severity, 0), reverse=True)

        logger.info(
            "license_agent: checked %d package(s) against project license %s, "
            "%d finding(s)",
            len(licenses),
            project_id,
            len(findings),
        )

        bundle = EvidenceBundle(
            domain=dispatch.domain,
            hypothesis=dispatch.hypothesis,
            packages_to_focus=[],
            findings=findings,
            summary=(
                f"Checked license compatibility for {len(licenses)} package(s) "
                f"against project license {project_id}. {len(findings)} finding(s)."
            ),
            confidence=1.0,
        )
        return bundle, ["license_collector", "license_rules"], 1
