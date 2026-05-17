"""Pure SBOM traversal functions for impact analysis.

These are not @tool-decorated — the analyze node wraps them as closures
so they can capture sbom_cyclonedx from state without passing it through
tool arguments.
"""

from __future__ import annotations


def _pkg_name(ref: str) -> str:
    """Extract package name from a bomRef or purl.

    Handles: "express@4.18.2", "@types/react@18.0.0",
             "pkg:npm/express@4.18.2", "pkg:npm/@scope/pkg@1.0.0"
    """
    if ref.startswith("pkg:npm/"):
        ref = ref[len("pkg:npm/"):]
    if ref.startswith("@"):
        slash = ref.find("/")
        if slash != -1:
            at = ref.find("@", slash)
            return ref[:at] if at != -1 else ref
        return ref
    return ref.split("@")[0]


def compute_direct_dependents(dep_name: str, sbom: dict) -> list[str]:
    """Return package names that directly depend on dep_name."""
    result = []
    for entry in sbom.get("dependencies", []):
        depends_on = entry.get("dependsOn", [])
        if any(_pkg_name(d) == dep_name for d in depends_on):
            name = _pkg_name(entry.get("ref", ""))
            if name:
                result.append(name)
    return result


def compute_blast_radius(dep_name: str, sbom: dict) -> dict:
    """BFS through the reverse dependency tree.

    Returns {direct_dependents, transitive_dependents, max_depth}.
    """
    # Build reverse map: name -> set of names that depend on it
    reverse: dict[str, set[str]] = {}
    for entry in sbom.get("dependencies", []):
        ref_name = _pkg_name(entry.get("ref", ""))
        for d in entry.get("dependsOn", []):
            d_name = _pkg_name(d)
            reverse.setdefault(d_name, set()).add(ref_name)

    frontier: set[str] = reverse.get(dep_name, set()).copy()
    if not frontier:
        return {"direct_dependents": 0, "transitive_dependents": 0, "max_depth": 0}

    direct_count = len(frontier)
    depth = 1
    visited: set[str] = frontier.copy()

    while True:
        next_frontier: set[str] = set()
        for pkg in frontier:
            next_frontier.update(reverse.get(pkg, set()) - visited)
        if not next_frontier:
            break
        visited.update(next_frontier)
        frontier = next_frontier
        depth += 1

    return {
        "direct_dependents": direct_count,
        "transitive_dependents": len(visited),
        "max_depth": depth,
    }
