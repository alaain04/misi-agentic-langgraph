# backend/src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py
"""Node: build_dependency_summary — generate metadata and LLM summary from CycloneDX."""

from typing import Any

from src.main_graph.subgraphs.discovery.state import (
    DiscoveryState,
    ProjectMetadata,
)
from src.utils.llm import Model, get_llm

_llm = get_llm(Model.GPT_5_4)

def _get_sbom_attribute(sbom: dict[str, Any], attribute: str) -> str:
    return sbom.get("metadata", {}).get("component", {}).get(attribute, "unknown")

def _extract_components(sbom: dict[str, Any], limit: int = 80) -> tuple[list[str], int]:
    """Return (component_strings, total_count) from SBOM components."""
    components = sbom.get("components", [])
    total = len(components)
    entries = []
    for c in components[:limit]:
        name = c.get("name", "")
        version = c.get("version", "")
        purl = c.get("purl", "")
        label = f"{name}@{version}" if version else name
        if purl and purl.startswith("pkg:"):
            ecosystem = purl.split(":")[1].split("/")[0]
            if ecosystem not in ("npm",):
                label += f" ({ecosystem})"
        entries.append(label)
    return entries, total


def _build_prompt(
    project_name: str,
    project_version: str,
    concern: str,
    components: list[str],
    total_count: int,
    lock_note: str = "",
) -> str:
    shown = len(components)
    truncation_note = (
        f" (showing {shown} of {total_count})" if total_count > shown else ""
    )
    component_list = "\n".join(f"  - {c}" for c in components) or "  (none)"
    version_label = f" v{project_version}" if project_version else ""

    return f"""\
You are analyzing the Node.js project's software bill of materials (SBOM).

Project: {project_name}{version_label}
Analysis concern: {concern}

Packages in SBOM{truncation_note}:
{component_list}{lock_note}

Write a concise summary (2-5 sentences / no more than 150 words) that:
- Identifies packages from the SBOM that are most relevant to the concern: "{concern}"
- Explains the potential impact those packages have on this specific project
- Flags any notable risks, compatibility issues, or dependencies that
  stand out given the concern
- Avoids generic ecosystem commentary; focus on what these specific
  packages mean for this project

Output only the summary text.\
"""


async def build_dependency_summary(state: DiscoveryState) -> dict:
    """Generate project metadata and an LLM summary from CycloneDX SBOM data."""
    error = state.get("discovery_error") or state.get("sbom_error")
    if error:
        return {
            "project_metadata": ProjectMetadata(
                name="unknown",
                package_manager="unknown",
                direct_dependencies_count=0,
            ),
            "discovery_summary": f"Discovery failed: {error}",
        }

    sbom: dict[str, Any] = state.get("sbom_cyclonedx", {})
    concern: str = state.get("concern", "")

    pm = state.get("detected_package_manager")
    project_name = _get_sbom_attribute(sbom, "name")
    project_version = _get_sbom_attribute(sbom, "version")
    components, total_count = _extract_components(sbom)

    metadata = ProjectMetadata(
        name=project_name,
        package_manager=pm,
        direct_dependencies_count=total_count,
    )

    lock_note = ""
    if state.get("lock_generation_error"):
        err = state["lock_generation_error"]
        lock_note = (
            f"\nNote: lock file generation failed ({err}); SBOM may be incomplete."
        )

    prompt = _build_prompt(
        project_name, project_version, concern, components, total_count, lock_note
    )
    response = await _llm.ainvoke(prompt)

    return {
        "project_metadata": metadata,
        "discovery_summary": response.content,
    }
