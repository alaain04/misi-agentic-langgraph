import json
import logging
import os

from src.db.input_cache import InputCacheDAO, cache_key, get_or_compute
from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.tools.trivy_cli import trivy_sbom_scan

logger = logging.getLogger(__name__)


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


def is_direct(graph: dict, name: str) -> bool:
    """True if `name` is a declared direct dependency in this graph."""
    return name in (graph.get("direct") or {})


def _package_name(flat_key: str) -> str:
    """Recover the package name from a "name@version" graph key, tolerating
    scoped names like "@scope/pkg@1.2.3"."""
    return flat_key.rsplit("@", 1)[0]


def direct_dependents(graph: dict, name: str) -> list[str]:
    """Return the direct dependencies whose subtree pulls in `name`, sorted."""
    direct = graph.get("direct") or {}
    packages = graph.get("packages") or {}

    if name in direct or not packages:
        return []

    direct_keys = {f"{n}@{v}" for n, v in direct.items()}
    parents: dict[str, set[str]] = {}

    for key, info in packages.items():
        for child in info.get("dependencies", []):
            parents.setdefault(child, set()).add(key)

    result: set[str] = set()
    seen: set[str] = set()
    stack = [k for k in packages if _package_name(k) == name]

    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        if key in direct_keys:
            result.add(_package_name(key))
        stack.extend(parents.get(key, ()))
    return sorted(result)


def _component_name(comp: dict) -> str | None:
    """Full package name for a CycloneDX component, recombining `group` with
    `name` for scoped npm packages -- Trivy splits "@scope/pkg" into
    group="@scope", name="pkg", and dropping `group` collapses a scoped
    package onto an unrelated top-level package sharing its bare name."""
    name = comp.get("name")
    if not name:
        return None
    group = comp.get("group")
    return f"{group}/{name}" if group else name


def _graph_from_cyclonedx(doc: dict) -> dict | None:
    """Adapt a Trivy CycloneDX document into the flat {"direct", "packages"}
    shape every consumer already expects (see build_dependency_graph's
    docstring for why the shape is flat, not nested).

    Trivy's CycloneDX output puts the scanned manifest file (package-lock.json
    / pnpm-lock.yaml / yarn.lock) itself in `components` as an
    "application"-typed node: the root workspace component (in
    `metadata.component`, not in `components`) dependsOn that manifest node,
    and the manifest node's own dependsOn IS the direct-dependency set.
    Verified directly against real npm and pnpm lockfiles — see
    docs/superpowers/plans/2026-07-31-trivy-adoption.md.

    Returns None when no manifest component was found (empty repo, trivy
    scan error, or an ecosystem Trivy doesn't recognize) so the caller can
    fall back to package.json-declared ranges.
    """
    components = doc.get("components") or []
    dependencies = doc.get("dependencies") or []
    if not components or not dependencies:
        return None

    by_ref = {c["bom-ref"]: c for c in components if c.get("bom-ref")}
    depends_on = {
        d["ref"]: d.get("dependsOn", []) for d in dependencies if d.get("ref")
    }

    root_ref = (doc.get("metadata") or {}).get("component", {}).get("bom-ref")
    manifest_ref = next(
        (
            r
            for r in depends_on.get(root_ref, [])
            if by_ref.get(r, {}).get("type") == "application"
        ),
        None,
    )
    if manifest_ref is None:
        return None

    direct_refs = depends_on.get(manifest_ref, [])
    direct = {
        _component_name(by_ref[r]): by_ref[r].get("version", "")
        for r in direct_refs
        if r in by_ref and _component_name(by_ref[r])
    }

    # Only include components reachable from this manifest -- a repo can have
    # more than one manifest (monorepo, or an unrelated manifest elsewhere in
    # the tree Trivy's recursive scan picked up), and packages under a
    # different manifest must not pool into this one's graph.
    reachable: set[str] = set()
    stack = list(direct_refs)
    while stack:
        ref = stack.pop()
        if ref in reachable or ref not in by_ref:
            continue
        reachable.add(ref)
        stack.extend(depends_on.get(ref, []))

    packages: dict[str, dict] = {}
    for ref in reachable:
        comp = by_ref[ref]
        if comp.get("type") == "application":
            continue
        name = _component_name(comp)
        if not name:
            continue
        version = comp.get("version", "")
        flat_key = f"{name}@{version}"
        children = sorted(
            f"{_component_name(by_ref[c])}@{by_ref[c].get('version', '')}"
            for c in depends_on.get(ref, [])
            if c in by_ref
            and by_ref[c].get("type") != "application"
            and _component_name(by_ref[c])
        )
        packages[flat_key] = {"version": version, "dependencies": children}

    return {"direct": direct, "packages": packages}


async def build_dependency_graph(
    repo_path: str,
    package_manager: str,
    container: ContainerRunPort,
    docker_image: str,
    # docker_image is currently unused: trivy_sbom_scan sources its scanner
    # image from settings.trivy_image internally, not from this parameter.
    pkg: dict | None = None,
    cache: InputCacheDAO | None = None,
    repo_url: str = "",
    commit_sha: str = "",
) -> dict:
    """Return each direct dependency plus a flat, deduplicated graph of every
    package reachable from them, backed by a Trivy CycloneDX scan.

    The output is a flat, deduplicated graph — {"direct": {name: version},
    "packages": {"name@version": {"version", "dependencies": [child_key,
    ...]}}} — not a nested tree. A nested tree duplicates every shared
    package's full subtree under each path that reaches it, which blows up
    combinatorially on real repos and is too large for MongoDB to store.

    Falls back to package.json-declared ranges (no transitive data) when the
    scan fails or finds no manifest, e.g. an empty repo or a scan error.

    When `cache`/`repo_url`/`commit_sha` are all provided, the underlying
    Trivy scan is cached by (repo_url, commit_sha, package_manager). Callers
    must only pass `cache` when the lockfile is a pure function of
    commit_sha (i.e. it was committed to the repo, not generated this run)
    — see save_prep_result's
    `lock_committed` check.
    """

    async def _scan() -> dict:
        return await trivy_sbom_scan(repo_path=repo_path, container=container)

    if cache is not None and repo_url and commit_sha:
        key = cache_key(repo_url, commit_sha, package_manager, "trivy_sbom")
        doc = await get_or_compute(cache, key, _scan)
    else:
        doc = await _scan()

    graph = None if "error" in doc else _graph_from_cyclonedx(doc)
    if graph is not None:
        return graph

    logger.warning(
        "build_dependency_graph: trivy scan unusable, pm=%s, falling back to "
        "package.json-declared ranges",
        package_manager,
    )
    pkg = pkg if pkg is not None else read_package_json(repo_path)
    direct_names = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    return {"direct": dict(direct_names), "packages": {}}
