"""Build the direct dependencies + flat package graph from a repo's lock file.

Each lock format already records, per installed package, which other
packages it depends on. Parsers return (installed, edges, root_keys): a
{key: (name, version)} map, a {key: [child_key, ...]} adjacency map, and an
optional {name: key} map for resolving each direct dependency's starting
key. `key` identifies one specific resolved install: for npm/yarn it's just
the package name (their lockfiles dedupe/hoist, so name is unambiguous in
practice); for pnpm it's the full snapshot key (name@version, optionally
peer-qualified e.g. "foo@1.0.0(react@18.0.0)"), since pnpm keeps distinct
coexisting resolutions of the same package as separate installs and
collapsing to name alone would silently merge them — which is also why pnpm
needs explicit root_keys instead of the generic first-seen-by-name fallback
build_dependency_graph uses for npm/yarn (root_keys=None).

The output is a flat, deduplicated graph — {"direct": {name: version},
"packages": {"name@version": {"version", "dependencies": [child_key, ...]}}}
— not a nested tree. A nested tree duplicates every shared package's full
subtree under each path that reaches it, which blows up combinatorially on
real repos (a shared low-level package like "tslib" can be a dependency of
dozens of others) and is too large for MongoDB to store. The flat form
stores each unique install exactly once; a tree view, if needed, can be
computed from it on the fly.
"""

import json
import logging
import os
import re

import yaml

logger = logging.getLogger(__name__)

_PNPM_KEY_RE = re.compile(r"^(@[^/@]+/[^@]+|[^@]+)@([^()]+)")
_MAX_TREE_DEPTH = 15

# (installed: {key: (name, version)}, edges: {key: [child_key, ...]},
#  root_keys: {name: key} | None)
_RootKeys = dict[str, str] | None
_ParsedLock = tuple[dict[str, tuple[str, str]], dict[str, list[str]], _RootKeys]


def read_package_json(repo_path: str) -> dict:
    path = os.path.join(repo_path, "package.json")
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def count_dependencies(graph: dict) -> tuple[int, int]:
    """Return (direct_count, unique_transitive_count) from a flat graph."""
    direct = graph.get("direct", {})
    packages = graph.get("packages", {})
    direct_keys = {f"{name}@{version}" for name, version in direct.items()}
    transitive_count = len(set(packages) - direct_keys)
    return len(direct), transitive_count


def build_dependency_graph(
    repo_path: str, package_manager: str, pkg: dict | None = None
) -> dict:
    """Read the lock file matching package_manager and return each direct
    dependency plus a flat, deduplicated graph of every package reachable
    from them.

    Falls back to package.json-declared ranges (no transitive data) when no
    lock file is found, e.g. install_deps failed to produce one.
    """
    pkg = pkg if pkg is not None else read_package_json(repo_path)
    direct_names = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

    parsers = {
        "npm": _parse_npm_lock,
        "pnpm": _parse_pnpm_lock,
        "yarn": _parse_yarn_lock,
    }
    parser = parsers.get(package_manager)
    parsed = parser(repo_path, direct_names) if parser else None

    if parsed is None:
        logger.warning("build_dependency_graph: no lock file, pm=%s", package_manager)
        return {"direct": dict(direct_names), "packages": {}}

    installed, edges, root_keys = parsed
    if root_keys is None:
        # Generic fallback: first-seen key per name. Only safe when keys
        # already are names (npm/yarn) — pnpm supplies its own root_keys
        # since multiple snapshots can share a name.
        root_keys = {}
        for key, (name, _version) in installed.items():
            root_keys.setdefault(name, key)

    direct = {}
    packages: dict[str, dict] = {}
    visited: set[str] = set()
    for name, declared in direct_names.items():
        key = root_keys.get(name)
        if key is None:
            direct[name] = declared
        else:
            direct[name] = installed[key][1]
            _collect_packages(key, installed, edges, packages, visited, 0)
    return {"direct": direct, "packages": packages}


def _collect_packages(
    key: str,
    installed: dict[str, tuple[str, str]],
    edges: dict[str, list[str]],
    packages: dict[str, dict],
    visited: set[str],
    depth: int,
) -> None:
    """DFS from `key`, recording each unique package once by "name@version".

    `visited` is global (not per-path), so a package already collected via
    one branch is skipped when reached again via another — this is what
    keeps output size at O(unique packages) instead of duplicating a
    shared package's subtree under every path that reaches it. The same
    check also breaks real cycles, since a key is marked visited before
    its children are walked.
    """
    name, version = installed[key]
    flat_key = f"{name}@{version}"
    if flat_key in visited or depth >= _MAX_TREE_DEPTH:
        return
    visited.add(flat_key)

    child_keys = []
    for child_key in edges.get(key, []):
        child = installed.get(child_key)
        if child is None:
            continue
        child_name, child_version = child
        child_keys.append(f"{child_name}@{child_version}")
        _collect_packages(child_key, installed, edges, packages, visited, depth + 1)

    packages[flat_key] = {"version": version, "dependencies": sorted(set(child_keys))}


def _parse_npm_lock(repo_path: str, direct_names: dict[str, str]) -> _ParsedLock | None:
    path = os.path.join(repo_path, "package-lock.json")
    try:
        with open(path) as f:
            lock = json.load(f)
    except Exception:
        return None

    packages = lock.get("packages")
    if packages is not None:
        installed, edges = {}, {}
        for key, entry in packages.items():
            if key == "" or "node_modules/" not in key:
                continue
            name = key.rsplit("node_modules/", 1)[-1]
            installed[name] = (name, entry.get("version", ""))
            deps = entry.get("dependencies", {})
            optional = entry.get("optionalDependencies", {})
            edges[name] = list({**deps, **optional})
        return installed, edges, None

    # lockfileVersion 1: top-level "dependencies" is the flattened install
    # tree; each entry's own "requires" lists its declared child names
    # ("dependencies" there means nested version-override entries instead).
    installed, edges = {}, {}
    for name, entry in lock.get("dependencies", {}).items():
        installed[name] = (name, entry.get("version", ""))
        edges[name] = list(entry.get("requires", {}))
    return installed, edges, None


def _parse_pnpm_lock(
    repo_path: str, direct_names: dict[str, str]
) -> _ParsedLock | None:
    """pnpm (lockfileVersion 9+) splits packages into two sections:
    "packages" holds only resolution/integrity metadata (no dependency
    edges), while "snapshots" holds the actual per-install dependency
    edges, keyed by a peer-qualified key (e.g. "foo@1.0.0(react@18.0.0)")
    that can differ from the bare "packages" key. Older lockfiles predate
    "snapshots" and keep edges inline on "packages" instead.

    root_keys comes straight from importers["."], since the same package
    name can have multiple snapshots (different peer contexts) and a
    generic first-seen-by-name scan could pick the wrong one for a direct
    dependency.
    """
    path = os.path.join(repo_path, "pnpm-lock.yaml")
    try:
        with open(path) as f:
            lock = yaml.safe_load(f) or {}
    except Exception:
        return None

    edge_source = lock.get("snapshots")
    if edge_source is None:
        edge_source = lock.get("packages") or {}

    installed: dict[str, tuple[str, str]] = {}
    edges: dict[str, list[str]] = {}
    for raw_key, entry in edge_source.items():
        name, version = _split_pnpm_key(raw_key)
        if not name:
            continue
        installed[raw_key] = (name, version)
        entry = entry or {}
        deps = entry.get("dependencies", {})
        optional = entry.get("optionalDependencies", {})
        edges[raw_key] = [f"{n}@{v}" for n, v in {**deps, **optional}.items()]

    importers = lock.get("importers")
    root_section = importers.get(".", {}) if importers else lock
    root_keys: dict[str, str] = {}
    for section in ("dependencies", "devDependencies"):
        for name, meta in (root_section.get(section) or {}).items():
            version = meta.get("version") if isinstance(meta, dict) else meta
            if version:
                root_keys[name] = f"{name}@{version}"

    return installed, edges, root_keys


def _split_pnpm_key(key: str) -> tuple[str | None, str | None]:
    if key.startswith("/"):
        name, _, version = key[1:].rpartition("/")
        return (name, version) if name else (None, None)
    m = _PNPM_KEY_RE.match(key)
    return (m.group(1), m.group(2)) if m else (None, None)


def _parse_yarn_lock(
    repo_path: str, direct_names: dict[str, str]
) -> _ParsedLock | None:
    """Dispatch to the Classic (v1) or Berry (v2+) parser. Berry's yarn.lock
    is valid YAML (a "__metadata" top-level key is its marker); Classic's
    isn't — e.g. its comma-grouped headers ("foo@^1.0.0, foo@^1.1.0:") and
    unquoted "version \"1.2.3\"" lines aren't YAML syntax at all.
    """
    path = os.path.join(repo_path, "yarn.lock")
    try:
        with open(path) as f:
            text = f.read()
    except Exception:
        return None

    try:
        parsed_yaml = yaml.safe_load(text)
    except yaml.YAMLError:
        parsed_yaml = None
    if isinstance(parsed_yaml, dict) and "__metadata" in parsed_yaml:
        return _parse_yarn_berry_lock(parsed_yaml, direct_names)
    return _parse_yarn_classic_lock(text.splitlines(keepends=True), direct_names)


def _parse_yarn_classic_lock(
    lines: list[str], direct_names: dict[str, str]
) -> _ParsedLock:
    """A header can group several descriptors ("foo@^1.0.0, foo@^1.1.0:")
    that resolve to one version, and different descriptor groups for the
    same name can resolve to different, coexisting versions — so, like
    pnpm, we key by resolved "name@version", not by name alone. Dependency
    lines and package.json only carry a declared range, so a
    descriptor->key index built from every header's descriptors is used to
    resolve both child edges and root_keys to the exact version yarn chose
    for that range.
    """
    blocks: list[dict] = []
    current: dict | None = None
    in_deps_block = False
    for raw_line in lines:
        if not raw_line.strip() or raw_line.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()

        if indent == 0:
            header = stripped.rstrip(":")
            descriptors = [d.strip().strip('"') for d in header.split(",")]
            current = {"descriptors": descriptors, "version": "", "deps": []}
            blocks.append(current)
            in_deps_block = False
        elif current is None:
            continue
        elif indent == 2:
            if stripped in ("dependencies:", "optionalDependencies:"):
                in_deps_block = True
            else:
                in_deps_block = False
                if stripped.startswith("version "):
                    current["version"] = stripped.split(" ", 1)[1].strip().strip('"')
        elif in_deps_block and indent >= 4:
            current["deps"].append(_yarn_dep_line_name_range(stripped))

    descriptor_to_key: dict[str, str] = {}
    installed: dict[str, tuple[str, str]] = {}
    for block in blocks:
        if not block["descriptors"] or not block["version"]:
            continue
        name = _yarn_descriptor_name(block["descriptors"][0])
        key = f"{name}@{block['version']}"
        installed[key] = (name, block["version"])
        block["_key"] = key
        for descriptor in block["descriptors"]:
            descriptor_to_key[descriptor] = key

    edges: dict[str, list[str]] = {}
    for block in blocks:
        if "_key" not in block:
            continue
        child_keys = []
        for child_name, child_range in block["deps"]:
            child_key = descriptor_to_key.get(f"{child_name}@{child_range}")
            if child_key:
                child_keys.append(child_key)
        edges[block["_key"]] = child_keys

    root_keys: dict[str, str] = {}
    for name, declared in direct_names.items():
        key = descriptor_to_key.get(f"{name}@{declared}")
        if key:
            root_keys[name] = key

    return installed, edges, root_keys


def _parse_yarn_berry_lock(
    lock: dict, direct_names: dict[str, str]
) -> _ParsedLock:
    """Berry stores per-package dependency ranges (not resolved versions),
    same as Classic — so root_keys/edges are resolved the same way, via a
    descriptor->key index. "resolution" (e.g. "foo@npm:1.2.3") is already
    the exact key format used elsewhere, so no reconstruction is needed
    the way pnpm's peer-suffixed snapshot keys require.
    """
    installed: dict[str, tuple[str, str]] = {}
    descriptor_to_key: dict[str, str] = {}
    entries: list[tuple[str, dict]] = []
    for raw_key, entry in lock.items():
        if raw_key == "__metadata" or not isinstance(entry, dict):
            continue
        resolution = entry.get("resolution")
        version = entry.get("version")
        if not resolution or version is None:
            continue
        name = _yarn_descriptor_name(resolution)
        installed[resolution] = (name, str(version))
        entries.append((resolution, entry))
        for descriptor in raw_key.split(","):
            descriptor_to_key[descriptor.strip()] = resolution

    edges: dict[str, list[str]] = {}
    for key, entry in entries:
        child_keys = []
        for child_name, child_range in (entry.get("dependencies") or {}).items():
            child_key = descriptor_to_key.get(f"{child_name}@npm:{child_range}")
            if child_key:
                child_keys.append(child_key)
        edges[key] = child_keys

    root_keys: dict[str, str] = {}
    for name, declared in direct_names.items():
        key = descriptor_to_key.get(f"{name}@npm:{declared}")
        if key:
            root_keys[name] = key

    return installed, edges, root_keys


def _yarn_descriptor_name(descriptor: str) -> str:
    if descriptor.startswith("@"):
        name, _, _range = descriptor[1:].partition("@")
        return "@" + name
    name, _, _range = descriptor.partition("@")
    return name


def _yarn_dep_line_name_range(line: str) -> tuple[str, str]:
    if line.startswith('"'):
        end = line.index('"', 1)
        name = line[1:end]
        rest = line[end + 1 :].strip()
    else:
        name, _, rest = line.partition(" ")
    return name, rest.strip().strip('"')
