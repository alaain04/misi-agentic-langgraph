from __future__ import annotations


def connected_groups(
    target_deps: list[str], requires_edges: dict[str, list[str]]
) -> list[list[str]]:
    """Compute connected groups of dependency names from discovered
    `requires` edges (target dep name -> list of other dep names it
    requires). Every name in `target_deps` appears in exactly one group -
    an unconnected target is a group of one (the common case). A name that
    only ever appears as a `requires` value, never independently in
    `target_deps`, is still included in whichever group pulled it in - it
    exists only because something else needs it (spec D8). Groups are
    sorted by their smallest member name, and each group's members are
    sorted, so output is deterministic and testable.
    """
    parent: dict[str, str] = {}

    def find(name: str) -> str:
        parent.setdefault(name, name)
        root = name
        while parent[root] != root:
            root = parent[root]
        while parent[name] != root:
            parent[name], name = root, parent[name]
        return root

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    all_names: set[str] = set(target_deps)
    for target, requires in requires_edges.items():
        all_names.add(target)
        all_names.update(requires)
    for name in all_names:
        find(name)
    for target, requires in requires_edges.items():
        for required in requires:
            union(target, required)

    groups: dict[str, list[str]] = {}
    for name in sorted(all_names):
        groups.setdefault(find(name), []).append(name)
    return sorted(groups.values(), key=lambda group: group[0])
