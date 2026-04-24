"""Node: build_dependency_summary — use an LLM to generate the dependency summary."""

from typing import Any

from src.graphs.project_discovery.state import (
    DependencyEntry,
    DiscoveryState,
    ProjectMetadata,
)
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
            entries.append(DependencyEntry(name=name, version_spec=spec, is_dev=False))
        for name, spec in data.get("dev_dependencies", {}).items():
            entries.append(DependencyEntry(name=name, version_spec=spec, is_dev=True))
        return entries  # only process the first package.json found

    for path, data in parsed_manifests.items():
        if data.get("format") != "pnpm-lock.yaml":
            continue
        for name, spec in data.get("dependencies", {}).items():
            entries.append(DependencyEntry(name=name, version_spec=spec, is_dev=False))
        for name, spec in data.get("dev_dependencies", {}).items():
            entries.append(DependencyEntry(name=name, version_spec=spec, is_dev=True))
        return entries

    return entries


def _get_project_name(parsed_manifests: dict[str, Any], repo_name: str) -> str:
    for _, data in parsed_manifests.items():
        if data.get("format") == "package.json" and data.get("name"):
            return data["name"]
    return repo_name


def _extract_transitive_info(parsed_manifests: dict[str, Any]) -> dict[str, Any]:
    """Extract transitive dependency info from whichever lock file was parsed."""
    for path, data in parsed_manifests.items():
        fmt = data.get("format")
        if fmt in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml"):
            return {
                "source": fmt,
                "resolved_count": data.get("resolved_packages_count", 0),
                "package_names": data.get("package_names", []),
            }
    return {"source": None, "resolved_count": 0, "package_names": []}


def _build_prompt(
    repo_owner: str,
    repo_name: str,
    concern: str,
    pm: str,
    direct_deps: list[DependencyEntry],
    transitive_info: dict[str, Any],
    manifest_files: list[str],
) -> str:
    prod_deps = [d for d in direct_deps if not d["is_dev"]]
    dev_deps = [d for d in direct_deps if d["is_dev"]]

    def _fmt_deps(deps: list[DependencyEntry], limit: int = 20) -> str:
        items = [f"{d['name']} ({d['version_spec']})" for d in deps[:limit]]
        result = ", ".join(items)
        if len(deps) > limit:
            result += f", … and {len(deps) - limit} more"
        return result or "none"

    transitive_section = ""
    if transitive_info["source"]:
        transitive_section = (
            f"\nLock file ({transitive_info['source']}): "
            f"{transitive_info['resolved_count']} total installed packages "
            f"(direct + transitive)"
        )
        names = transitive_info["package_names"]
        if names:
            sample = ", ".join(names[:30])
            if len(names) > 30:
                sample += f", … and {len(names) - 30} more"
            transitive_section += f"\nSample installed packages: {sample}"

    return f"""\
You are analysing a GitHub repository's JavaScript/Node.js dependency structure.

Repository: {repo_owner}/{repo_name}
Package manager: {pm}
Manifest files found: {", ".join(manifest_files) or "none"}
Analysis concern: {concern}

Direct production dependencies ({len(prod_deps)}): {_fmt_deps(prod_deps)}
Direct dev dependencies ({len(dev_deps)}): {_fmt_deps(dev_deps, 10)}{transitive_section}

Write a concise dependency discovery summary (3-5 sentences) that covers:
1. What the project uses for package management and how many direct dependencies it has
2. The transitive dependency chain installed (total packages from lock file), if 
available
3. Notable packages relevant to the concern: "{concern}"

Be factual and specific. Output only the summary text, no headings or bullet points."""


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
            "discovery_summary": f"Discovery failed: {state['discovery_error']}",
        }

    parsed_manifests: dict[str, Any] = state.get("parsed_manifests", {})
    repo_owner: str = state.get("repo_owner", "")
    repo_name: str = state.get("repo_name", "")
    manifest_files: list[str] = state.get("manifest_files", [])
    concern: str = state.get("concern", "")

    pm = _detect_package_manager(parsed_manifests)
    direct_deps = _collect_direct_dependencies(parsed_manifests)
    project_name = _get_project_name(parsed_manifests, repo_name)
    transitive_info = _extract_transitive_info(parsed_manifests)

    metadata = ProjectMetadata(
        name=project_name,
        package_manager=pm,
        direct_dependencies_count=len(direct_deps),
    )

    prompt = _build_prompt(
        repo_owner, repo_name, concern, pm, direct_deps, transitive_info, manifest_files
    )
    response = await _llm.ainvoke(prompt)

    return {
        "project_metadata": metadata,
        "direct_dependencies": direct_deps,
        "discovery_summary": response.content,
    }
