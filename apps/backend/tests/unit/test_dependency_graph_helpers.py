from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.main_graph.subgraphs.discovery.dependency_graph import (
    build_dependency_graph,
    dependents_of,
    direct_dependents,
    is_direct,
)

_GRAPH = {
    "direct": {"express": "4.18.0", "webpack": "5.0.0"},
    "packages": {
        "express@4.18.0": {
            "version": "4.18.0",
            "dependencies": ["body-parser@1.20.0"],
        },
        "body-parser@1.20.0": {
            "version": "1.20.0",
            "dependencies": ["qs@6.11.0"],
        },
        "qs@6.11.0": {"version": "6.11.0", "dependencies": []},
        "webpack@5.0.0": {"version": "5.0.0", "dependencies": ["qs@6.11.0"]},
    },
}


def test_is_direct_true_for_declared_dependency():
    assert is_direct(_GRAPH, "express") is True


def test_is_direct_false_for_transitive():
    assert is_direct(_GRAPH, "qs") is False


def test_is_direct_false_when_absent():
    assert is_direct(_GRAPH, "not-installed") is False


def test_direct_dependents_empty_for_direct_dependency():
    assert direct_dependents(_GRAPH, "express") == []


def test_direct_dependents_single_parent():
    assert direct_dependents(_GRAPH, "body-parser") == ["express"]


def test_direct_dependents_shared_transitive_lists_all_sorted():
    # qs is pulled by express (via body-parser) and directly by webpack
    assert direct_dependents(_GRAPH, "qs") == ["express", "webpack"]


def test_direct_dependents_scoped_package_name():
    graph = {
        "direct": {"@nestjs/core": "10.0.0"},
        "packages": {
            "@nestjs/core@10.0.0": {
                "version": "10.0.0",
                "dependencies": ["@scope/leaf@1.0.0"],
            },
            "@scope/leaf@1.0.0": {"version": "1.0.0", "dependencies": []},
        },
    }
    assert direct_dependents(graph, "@scope/leaf") == ["@nestjs/core"]


def test_direct_dependents_empty_when_no_transitive_data():
    # package.json fallback: direct names but no packages graph
    graph = {"direct": {"lodash": "^4.17.21"}, "packages": {}}
    assert direct_dependents(graph, "some-transitive") == []


def test_direct_dependents_empty_graph():
    assert direct_dependents({}, "anything") == []


def test_dependents_of_returns_immediate_parents_only():
    # qs is depended on directly by body-parser and webpack (not express,
    # which only reaches qs transitively through body-parser)
    assert dependents_of(_GRAPH, "qs") == ["body-parser", "webpack"]


def test_dependents_of_single_parent():
    assert dependents_of(_GRAPH, "body-parser") == ["express"]


def test_dependents_of_empty_for_a_leaf_nothing_depends_on():
    assert dependents_of(_GRAPH, "express") == []


def test_dependents_of_empty_when_no_transitive_data():
    graph = {"direct": {"lodash": "^4.17.21"}, "packages": {}}
    assert dependents_of(graph, "lodash") == []


def test_dependents_of_empty_graph():
    assert dependents_of({}, "anything") == []


def test_dependents_of_scoped_package_name():
    graph = {
        "direct": {"@nestjs/core": "10.0.0"},
        "packages": {
            "@nestjs/core@10.0.0": {"dependencies": ["@scope/leaf@1.0.0"]},
            "@scope/leaf@1.0.0": {"dependencies": []},
        },
    }
    assert dependents_of(graph, "@scope/leaf") == ["@nestjs/core"]


def _cyclonedx_doc(
    *,
    manifest_name: str,
    direct: list[dict],
    transitive_edges: dict[str, list[str]] | None = None,
) -> dict:
    """Build a minimal CycloneDX doc matching Trivy's verified shape: a root
    metadata.component, an "application"-typed manifest component the root
    depends on, and the manifest depending on the direct set.

    `transitive_edges` maps a direct dep's bom-ref to the bom-refs of its own
    children — the caller is responsible for also appending those child
    components/dependency entries to the returned doc (see
    test_build_dependency_graph_includes_transitive_edges for the pattern),
    since this helper only knows about the direct-dependency layer.
    """
    root_ref = "root-ref"
    manifest_ref = "manifest-ref"
    components = [
        {"bom-ref": manifest_ref, "type": "application", "name": manifest_name}
    ]
    dependencies = [
        {"ref": root_ref, "dependsOn": [manifest_ref]},
        {"ref": manifest_ref, "dependsOn": [d["bom-ref"] for d in direct]},
    ]
    for d in direct:
        components.append(
            {
                "bom-ref": d["bom-ref"],
                "type": "library",
                "name": d["name"],
                "version": d["version"],
            }
        )
        dependencies.append(
            {
                "ref": d["bom-ref"],
                "dependsOn": (transitive_edges or {}).get(d["bom-ref"], []),
            }
        )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "metadata": {"component": {"name": "/workspace", "bom-ref": root_ref}},
        "components": components,
        "dependencies": dependencies,
    }


@pytest.mark.asyncio
async def test_build_dependency_graph_adapts_cyclonedx_direct_deps():
    doc = _cyclonedx_doc(
        manifest_name="package-lock.json",
        direct=[
            {
                "bom-ref": "pkg:npm/express@4.22.2",
                "name": "express",
                "version": "4.22.2",
            }
        ],
    )
    container = AsyncMock()
    container.run.return_value = (0, __import__("json").dumps(doc), "")

    graph = await build_dependency_graph(
        repo_path="/tmp/repo",
        package_manager="npm",
        container=container,
        docker_image="aquasec/trivy:0.71.2",
    )

    assert graph["direct"] == {"express": "4.22.2"}
    assert "express@4.22.2" in graph["packages"]


@pytest.mark.asyncio
async def test_build_dependency_graph_includes_transitive_edges():
    doc = _cyclonedx_doc(
        manifest_name="package-lock.json",
        direct=[
            {
                "bom-ref": "pkg:npm/express@4.22.2",
                "name": "express",
                "version": "4.22.2",
            }
        ],
        transitive_edges={"pkg:npm/express@4.22.2": ["pkg:npm/accepts@1.3.8"]},
    )
    doc["components"].append(
        {
            "bom-ref": "pkg:npm/accepts@1.3.8",
            "type": "library",
            "name": "accepts",
            "version": "1.3.8",
        }
    )
    doc["dependencies"].append({"ref": "pkg:npm/accepts@1.3.8", "dependsOn": []})
    container = AsyncMock()
    container.run.return_value = (0, __import__("json").dumps(doc), "")

    graph = await build_dependency_graph(
        repo_path="/tmp/repo",
        package_manager="npm",
        container=container,
        docker_image="aquasec/trivy:0.71.2",
    )

    assert graph["packages"]["express@4.22.2"]["dependencies"] == ["accepts@1.3.8"]
    assert "accepts@1.3.8" in graph["packages"]


@pytest.mark.asyncio
async def test_build_dependency_graph_falls_back_to_package_json_on_scan_error(
    tmp_path,
):
    (tmp_path / "package.json").write_text('{"dependencies": {"express": "^4.18.0"}}')
    container = AsyncMock()
    container.run.return_value = (127, "", "sh: trivy: not found")

    graph = await build_dependency_graph(
        repo_path=str(tmp_path),
        package_manager="npm",
        container=container,
        docker_image="aquasec/trivy:0.71.2",
    )

    assert graph == {"direct": {"express": "^4.18.0"}, "packages": {}}


@pytest.mark.asyncio
async def test_build_dependency_graph_falls_back_when_no_manifest_detected(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    container = AsyncMock()
    empty_doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "metadata": {},
        "components": [],
        "dependencies": [],
    }
    container.run.return_value = (0, __import__("json").dumps(empty_doc), "")

    graph = await build_dependency_graph(
        repo_path=str(tmp_path),
        package_manager="npm",
        container=container,
        docker_image="aquasec/trivy:0.71.2",
    )

    assert graph == {"direct": {}, "packages": {}}


@pytest.mark.asyncio
async def test_build_dependency_graph_uses_cache_when_available():
    from src.db.input_cache import cache_key

    cached_graph_doc = _cyclonedx_doc(manifest_name="package-lock.json", direct=[])

    class _FakeCache:
        def __init__(self):
            self.store = {
                cache_key(
                    "https://github.com/x/y", "sha1", "npm", "trivy_sbom"
                ): cached_graph_doc
            }

        async def get(self, key, max_age_seconds=None):
            return self.store.get(key)

        async def put(self, key, data):
            self.store[key] = data

    container = AsyncMock()
    graph = await build_dependency_graph(
        repo_path="/tmp/repo",
        package_manager="npm",
        container=container,
        docker_image="aquasec/trivy:0.71.2",
        cache=_FakeCache(),
        repo_url="https://github.com/x/y",
        commit_sha="sha1",
    )

    container.run.assert_not_awaited()
    assert graph == {"direct": {}, "packages": {}}


@pytest.mark.asyncio
async def test_build_dependency_graph_preserves_scoped_package_names():
    """Trivy's CycloneDX output splits a scoped npm package's purl into a
    `group` field ("@nestjs") and an unscoped `name` ("core"). The direct
    and packages keys must recombine them into the full "@nestjs/core" name
    findings/tools use elsewhere -- dropping `group` silently collapses a
    scoped package onto an unrelated top-level package of the same bare
    name (e.g. "@nestjs/config" onto npm's unrelated "config" package).
    """
    root_ref = "root-ref"
    manifest_ref = "manifest-ref"
    core_ref = "pkg:npm/%40nestjs/core@10.4.15"
    leaf_ref = "pkg:npm/leaf@1.0.0"
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "metadata": {"component": {"name": "/workspace", "bom-ref": root_ref}},
        "components": [
            {
                "bom-ref": manifest_ref,
                "type": "application",
                "name": "package-lock.json",
            },
            {
                "bom-ref": core_ref,
                "type": "library",
                "group": "@nestjs",
                "name": "core",
                "version": "10.4.15",
            },
            {
                "bom-ref": leaf_ref,
                "type": "library",
                "name": "leaf",
                "version": "1.0.0",
            },
        ],
        "dependencies": [
            {"ref": root_ref, "dependsOn": [manifest_ref]},
            {"ref": manifest_ref, "dependsOn": [core_ref]},
            {"ref": core_ref, "dependsOn": [leaf_ref]},
            {"ref": leaf_ref, "dependsOn": []},
        ],
    }
    container = AsyncMock()
    container.run.return_value = (0, __import__("json").dumps(doc), "")

    graph = await build_dependency_graph(
        repo_path="/tmp/repo",
        package_manager="npm",
        container=container,
        docker_image="aquasec/trivy:0.71.2",
    )

    assert graph["direct"] == {"@nestjs/core": "10.4.15"}
    assert "@nestjs/core@10.4.15" in graph["packages"]
    assert graph["packages"]["@nestjs/core@10.4.15"]["dependencies"] == ["leaf@1.0.0"]


@pytest.mark.asyncio
async def test_build_dependency_graph_does_not_pool_packages_across_manifests():
    """A repo can contain more than one manifest (monorepo with two
    lockfiles, or an unrelated manifest elsewhere in the tree that Trivy's
    recursive scan picked up). Only the first-picked manifest's own
    dependsOn subtree should end up in `packages` -- a second, disjoint
    manifest's packages must not leak in even though they're present in the
    same CycloneDX document.
    """
    root_ref = "root-ref"
    manifest1_ref = "manifest1-ref"
    manifest2_ref = "manifest2-ref"
    alpha_ref = "pkg:npm/alpha@1.0.0"
    alpha_dep_ref = "pkg:npm/alpha-dep@1.0.0"
    beta_ref = "pkg:npm/beta@1.0.0"
    beta_dep_ref = "pkg:npm/beta-dep@1.0.0"

    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "metadata": {"component": {"name": "/workspace", "bom-ref": root_ref}},
        "components": [
            {
                "bom-ref": manifest1_ref,
                "type": "application",
                "name": "package-lock.json",
            },
            {
                "bom-ref": manifest2_ref,
                "type": "application",
                "name": "services/other/package-lock.json",
            },
            {
                "bom-ref": alpha_ref,
                "type": "library",
                "name": "alpha",
                "version": "1.0.0",
            },
            {
                "bom-ref": alpha_dep_ref,
                "type": "library",
                "name": "alpha-dep",
                "version": "1.0.0",
            },
            {
                "bom-ref": beta_ref,
                "type": "library",
                "name": "beta",
                "version": "1.0.0",
            },
            {
                "bom-ref": beta_dep_ref,
                "type": "library",
                "name": "beta-dep",
                "version": "1.0.0",
            },
        ],
        "dependencies": [
            {"ref": root_ref, "dependsOn": [manifest1_ref, manifest2_ref]},
            {"ref": manifest1_ref, "dependsOn": [alpha_ref]},
            {"ref": manifest2_ref, "dependsOn": [beta_ref]},
            {"ref": alpha_ref, "dependsOn": [alpha_dep_ref]},
            {"ref": alpha_dep_ref, "dependsOn": []},
            {"ref": beta_ref, "dependsOn": [beta_dep_ref]},
            {"ref": beta_dep_ref, "dependsOn": []},
        ],
    }
    container = AsyncMock()
    container.run.return_value = (0, __import__("json").dumps(doc), "")

    graph = await build_dependency_graph(
        repo_path="/tmp/repo",
        package_manager="npm",
        container=container,
        docker_image="aquasec/trivy:0.71.2",
    )

    assert graph["direct"] == {"alpha": "1.0.0"}
    assert set(graph["packages"]) == {"alpha@1.0.0", "alpha-dep@1.0.0"}
    assert "beta@1.0.0" not in graph["packages"]
    assert "beta-dep@1.0.0" not in graph["packages"]
