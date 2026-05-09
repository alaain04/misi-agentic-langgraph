"""Node: build_dependency_summary — use an LLM to generate the dependency summary."""

from typing import Any

from typing_extensions import TypedDict

from src.main_graph.subgraphs.discovery.state import (
    DiscoveryState,
    ProjectMetadata,
)


class DependencyEntry(TypedDict):
    name: str
    version_spec: str
from src.utils.llm import Model, get_llm

_llm = get_llm(Model.GPT_4O_MINI)

# Determines which lock file wins for package manager detection.
_PM_PRIORITY = {
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "package-lock.json": "npm",
}


def _detect_package_manager(parsed_manifests: dict[str, Any]) -> str:
    """Return the package manager inferred from the lock files present."""
    for path in parsed_manifests:
        filename = path.split("/")[-1]
        pm = _PM_PRIORITY.get(filename)
        if pm:
            return pm
    return "npm"  # package.json without a lock file → assume npm


def _collect_direct_dependencies(
    parsed_manifests: dict[str, Any],
) -> list[DependencyEntry]:
    """
    Extract direct (non-transitive) dependencies from package.json.
    Falls back to pnpm-lock.yaml top-level deps if package.json is absent.
    """
    entries: list[DependencyEntry] = []

    for path, data in parsed_manifests.items():
        if data.get("format") != "package.json":
            continue
        for name, spec in data.get("dependencies", {}).items():
            entries.append(DependencyEntry(name=name, version_spec=spec))
        for name, spec in data.get("dev_dependencies", {}).items():
            entries.append(DependencyEntry(name=name, version_spec=spec))
        return entries  # only process the first package.json found

    for path, data in parsed_manifests.items():
        if data.get("format") != "pnpm-lock.yaml":
            continue
        for name, spec in data.get("dependencies", {}).items():
            entries.append(DependencyEntry(name=name, version_spec=spec))
        for name, spec in data.get("dev_dependencies", {}).items():
            entries.append(DependencyEntry(name=name, version_spec=spec))
        return entries

    return entries


def _get_project_name(parsed_manifests: dict[str, Any]) -> str:
    for _, data in parsed_manifests.items():
        if data.get("format") == "package.json" and data.get("name"):
            return data["name"]
    return "unknown"


def _build_dependency_tree(
    direct_deps: list[DependencyEntry],
    resolved: dict[str, str],
    edges: dict[str, list[str]],
) -> dict[str, Any]:
    """
    Recursively build a dependency tree starting from direct_deps.
    Cycles are detected per-path and marked with "circular": True.
    """

    def _node(name: str, path: frozenset[str]) -> dict[str, Any]:
        version = resolved.get(name, "")
        if name in path:
            return {"version": version, "circular": True}
        children = edges.get(name, [])
        new_path = path | {name}
        return {
            "version": version,
            "deps": {child: _node(child, new_path) for child in children},
        }

    return {dep["name"]: _node(dep["name"], frozenset()) for dep in direct_deps}


def _collect_lock_data(
    parsed_manifests: dict[str, Any],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Return (resolved_packages, package_edges) from the first lock file found."""
    for data in parsed_manifests.values():
        if data.get("format") in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml"):
            return data.get("resolved_packages", {}), data.get("package_edges", {})
    return {}, {}


def _collect_transitive_dependencies(
    parsed_manifests: dict[str, Any],
    direct_deps: list[DependencyEntry],
) -> list[DependencyEntry]:
    """
    Build the list of transitive dependencies from the lock file.
    Transitive = all resolved packages minus the direct dependencies.
    """
    direct_names = {d["name"] for d in direct_deps}

    for data in parsed_manifests.values():
        if data.get("format") in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml"):
            resolved: dict[str, str] = data.get("resolved_packages", {})
            return [
                DependencyEntry(name=name, version_spec=version)
                for name, version in resolved.items()
                if name not in direct_names
            ]
    return []


def _build_prompt(
    project_name: str,
    concern: str,
    pm: str,
    direct_deps: list[DependencyEntry],
    transitive_deps: list[DependencyEntry],
    manifest_files: list[str],
) -> str:
    def _fmt_deps(deps: list[DependencyEntry], limit: int = 20) -> str:
        items = [f"{d['name']} ({d['version_spec']})" for d in deps[:limit]]
        result = ", ".join(items)
        if len(deps) > limit:
            result += f", … and {len(deps) - limit} more"
        return result or "none"

    transitive_section = ""
    if transitive_deps:
        sample = _fmt_deps(transitive_deps, 30)
        transitive_section = (
            f"\nTransitive dependencies ({len(transitive_deps)}): {sample}"
        )

    return f"""\
You are analyzing the dependency structure of a JavaScript/Node.js project.

Project: {project_name}
Package manager: {pm}
Manifest files: {", ".join(manifest_files) or "none"}
Analysis concern: {concern}

Direct dependencies:
{_fmt_deps(direct_deps)}

{transitive_section}

Write a concise summary (3–5 sentences) that:
- Describes the package management approach and overall dependency structure
- Characterizes the ecosystem (e.g., frontend-heavy, backend services,
  tooling, build system, etc.)
- Highlights notable dependencies that are relevant to the concern: "{concern}"
- Explains *why* those dependencies matter in this context
  (risk, exposure, maintenance, performance, etc.)

Focus on interpretation over enumeration. Do not list all dependencies or
repeat raw counts unless necessary for context. Avoid generic statements and
make the analysis specific to this project.
Output only the summary text.\
"""


async def build_dependency_summary(state: DiscoveryState) -> dict:
    """
    Use an LLM to generate a natural-language discovery_summary from all
    parsed manifest data, including transitive dependencies from lock files.
    """
    if state.get("discovery_error"):
        return {
            "project_metadata": ProjectMetadata(
                name="unknown",
                package_manager="unknown",
                direct_dependencies_count=0,
            ),
            "direct_dependencies": [],
            "transitive_dependencies": [],
            "dependency_tree": {},
            "discovery_summary": f"Discovery failed: {state['discovery_error']}",
        }

    parsed_manifests: dict[str, Any] = state.get("parsed_manifests", {})
    manifest_files: list[str] = state.get("manifest_files", [])
    concern: str = state.get("concern", "")

    pm = _detect_package_manager(parsed_manifests)
    direct_deps = _collect_direct_dependencies(parsed_manifests)
    resolved, edges = _collect_lock_data(parsed_manifests)
    transitive_deps = _collect_transitive_dependencies(parsed_manifests, direct_deps)
    project_name = _get_project_name(parsed_manifests)
    dep_tree = _build_dependency_tree(direct_deps, resolved, edges)

    metadata = ProjectMetadata(
        name=project_name,
        package_manager=pm,
        direct_dependencies_count=len(direct_deps),
    )

    prompt = _build_prompt(
        project_name, concern, pm, direct_deps, transitive_deps, manifest_files
    )
    response = await _llm.ainvoke(prompt)

    return {
        "project_metadata": metadata,
        "direct_dependencies": direct_deps,
        "transitive_dependencies": transitive_deps,
        "dependency_tree": dep_tree,
        "discovery_summary": response.content,
    }
