# backend/src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py
"""Node: build_dependency_summary — generate metadata and LLM summary from CycloneDX."""

from typing import Any

from src.main_graph.subgraphs.discovery.state import (
    DiscoveryState,
    ProjectMetadata,
)
from src.utils.llm import Model, get_llm

_llm = get_llm(Model.GPT_4O_MINI)


def _get_root_ref(sbom: dict[str, Any]) -> str:
    root = sbom.get("metadata", {}).get("component", {})
    return root.get("bom-ref") or root.get("name", "")


def _get_direct_dep_refs(sbom: dict[str, Any], root_ref: str) -> set[str]:
    for entry in sbom.get("dependencies", []):
        if entry.get("ref") == root_ref:
            return set(entry.get("dependsOn", []))
    return set()


def _get_project_name(sbom: dict[str, Any]) -> str:
    return sbom.get("metadata", {}).get("component", {}).get("name", "unknown")


def _detect_package_manager(manifest_files: list[str]) -> str:
    for f in manifest_files:
        if "pnpm" in f:
            return "pnpm"
        if "yarn" in f:
            return "yarn"
        if "package-lock" in f:
            return "npm"
    return "npm"


def _build_prompt(
    project_name: str,
    concern: str,
    pm: str,
    direct_names: list[str],
    direct_count: int,
    total_count: int,
    transitive_count: int,
    lock_note: str = "",
) -> str:
    def _fmt(names: list[str], limit: int = 20) -> str:
        items = names[:limit]
        result = ", ".join(items)
        if len(names) > limit:
            result += f", ... and {len(names) - limit} more"
        return result or "none"

    return f"""\
You are analyzing the dependency structure of a JavaScript/Node.js project.

Project: {project_name}
Package manager: {pm}
Direct dependencies ({direct_count}): {_fmt(direct_names)}
Transitive dependencies: {transitive_count}
Total components: {total_count}
Analysis concern: {concern}{lock_note}

Write a concise summary (3-5 sentences) that:
- Describes the package management approach and overall dependency structure
- Characterizes the ecosystem (e.g., frontend-heavy, backend services, tooling, etc.)
- Highlights notable dependencies relevant to the concern: "{concern}"
- Explains why those dependencies matter in this context

Focus on interpretation over enumeration. Do not list all dependencies.
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
    manifest_files: list[str] = state.get("manifest_files", [])
    concern: str = state.get("concern", "")

    root_ref = _get_root_ref(sbom)
    direct_refs = _get_direct_dep_refs(sbom, root_ref)
    components = sbom.get("components", [])

    direct_names = [c["name"] for c in components if c.get("bom-ref") in direct_refs]
    transitive_count = len(components) - len(direct_refs)

    pm = _detect_package_manager(manifest_files)
    project_name = _get_project_name(sbom)

    metadata = ProjectMetadata(
        name=project_name,
        package_manager=pm,
        direct_dependencies_count=len(direct_refs),
    )

    lock_note = ""
    if state.get("lock_generation_error"):
        lock_note = f"\nNote: lock file generation failed ({state['lock_generation_error']}); transitive dependencies may be incomplete."

    prompt = _build_prompt(
        project_name, concern, pm, direct_names,
        len(direct_refs), len(components), transitive_count,
        lock_note=lock_note,
    )
    response = await _llm.ainvoke(prompt)

    return {
        "project_metadata": metadata,
        "discovery_summary": response.content,
    }
