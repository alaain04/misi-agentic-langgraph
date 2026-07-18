"""Resolve installed package versions from the discovery dependency graph.

`dependency_graph` is the flat structure produced by
`discovery.dependency_graph.build_dependency_graph`: {"direct": {name:
version}, "packages": {"name@version": {...}}}. Direct dependencies resolve
in O(1); transitive ones require a scan of "packages" since that dict is
keyed by "name@version", not by name alone.
"""

from __future__ import annotations


def resolve_installed_versions(
    names: list[str], dependency_graph: dict
) -> dict[str, str]:
    direct = dependency_graph.get("direct", {})
    packages = dependency_graph.get("packages", {})
    resolved: dict[str, str] = {}
    for name in names:
        version = direct.get(name)
        if version is None:
            for key in packages:
                if key.rsplit("@", 1)[0] == name:
                    version = packages[key].get("version")
                    break
        if version:
            resolved[name] = version
    return resolved
