"""Tests for the deterministic discovery nodes: clone_repo, inspect_repo,
install_deps."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.clone_repo import clone_repo
from src.main_graph.subgraphs.discovery.nodes.inspect_repo import inspect_repo
from src.main_graph.subgraphs.discovery.nodes.install_deps import install_deps

_BASE_STATE = {
    "job_id": "test-job",
    "repo_url": "https://github.com/test/repo",
    "concern": "security",
    "autopilot": False,
}


def _config(container=None):
    return {
        "configurable": {
            "container": container or AsyncMock(),
        }
    }


# ---------------------------------------------------------------------------
# clone_repo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clone_repo_success(tmp_path):
    container = AsyncMock()
    container.run.return_value = (0, "", "")

    with patch("src.main_graph.subgraphs.discovery.nodes.clone_repo.os.makedirs"):
        with patch(
            "src.main_graph.subgraphs.discovery.nodes.clone_repo.os.path.abspath",
            return_value=str(tmp_path),
        ):
            result = await clone_repo(_BASE_STATE, _config(container=container))

    assert result["repo_path"] == str(tmp_path)
    assert "discovery_error" not in result
    # two container runs: git clone, then git rev-parse HEAD for the commit sha
    assert container.run.await_count == 2


@pytest.mark.asyncio
async def test_clone_repo_failure_sets_discovery_error(tmp_path):
    container = AsyncMock()
    container.run.return_value = (128, "", "repository not found")

    with patch("src.main_graph.subgraphs.discovery.nodes.clone_repo.os.makedirs"):
        with patch(
            "src.main_graph.subgraphs.discovery.nodes.clone_repo.os.path.abspath",
            return_value=str(tmp_path),
        ):
            result = await clone_repo(_BASE_STATE, _config(container=container))

    assert result["discovery_error"] == "repository not found"
    assert result["repo_path"] == str(tmp_path)


# ---------------------------------------------------------------------------
# inspect_repo
# ---------------------------------------------------------------------------


def test_inspect_repo_with_package_lock(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "myapp", "engines": {"node": ">=20"}})
    )
    (tmp_path / "package-lock.json").write_text("{}")

    result = inspect_repo({**_BASE_STATE, "repo_path": str(tmp_path)})

    assert result["detected_package_manager"] == "npm"
    assert result["has_lock_file"] is True
    assert result["docker_image"] == "node:20-alpine"
    assert "package-lock.json" in result["manifest_files"]


def test_inspect_repo_with_pnpm_lock(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "myapp", "packageManager": "pnpm@9.0.0"})
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'")

    result = inspect_repo({**_BASE_STATE, "repo_path": str(tmp_path)})

    assert result["detected_package_manager"] == "pnpm"
    assert result["has_lock_file"] is True
    assert result["package_manager_version"] == "9.0.0"


def test_inspect_repo_pnpm_v11_requires_node22(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"packageManager": "pnpm@11.0.0", "engines": {"node": ">=18"}})
    )
    (tmp_path / "pnpm-lock.yaml").write_text("")

    result = inspect_repo({**_BASE_STATE, "repo_path": str(tmp_path)})

    assert result["docker_image"] == "node:22-alpine"


def test_inspect_repo_no_lock_file(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "myapp"}))

    result = inspect_repo({**_BASE_STATE, "repo_path": str(tmp_path)})

    assert result["has_lock_file"] is False
    assert result["detected_package_manager"] == "npm"


def test_inspect_repo_packagemanager_field_sets_pm_when_no_lock(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"packageManager": "pnpm@8.1.0+sha512.abc"})
    )

    result = inspect_repo({**_BASE_STATE, "repo_path": str(tmp_path)})

    assert result["detected_package_manager"] == "pnpm"
    assert result["package_manager_version"] == "8.1.0"
    assert result["has_lock_file"] is False


# ---------------------------------------------------------------------------
# install_deps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_deps_creates_lock_file(tmp_path):
    container = AsyncMock()
    container.run.return_value = (0, "", "")
    lock_path = tmp_path / "package-lock.json"
    lock_path.write_text("{}")  # simulate install created it

    state = {
        **_BASE_STATE,
        "repo_path": str(tmp_path),
        "detected_package_manager": "npm",
        "package_manager_version": "latest",
        "docker_image": "node:20-alpine",
    }
    result = await install_deps(state, _config(container=container))

    assert result["has_lock_file"] is True
    # install_deps ran, so the lock was generated this run (not committed) —
    # signals that the dependency graph must not be cached indefinitely.
    assert result["lockfile_generated"] is True
    # Lock already present after the normal install — no lockfile-only fallback needed.
    assert container.run.await_count == 1


@pytest.mark.asyncio
async def test_install_deps_no_lock_created(tmp_path):
    container = AsyncMock()
    container.run.return_value = (0, "", "")

    state = {
        **_BASE_STATE,
        "repo_path": str(tmp_path),
        "detected_package_manager": "npm",
        "package_manager_version": "latest",
        "docker_image": "node:20-alpine",
    }
    result = await install_deps(state, _config(container=container))

    assert result["has_lock_file"] is False


@pytest.mark.asyncio
async def test_install_deps_npm_falls_back_to_lockfile_only(tmp_path):
    """Some repos' normal `npm install` completes (rc=0) without ever writing
    a lock file (e.g. an .npmrc disabling it). install_deps must force one
    explicitly so the dependency graph still gets transitive data."""
    lock_path = tmp_path / "package-lock.json"

    def _run_side_effect(*, image, command, volume, run_as_root):
        if "--package-lock-only" in command:
            lock_path.write_text("{}")
        return (0, "", "")

    container = AsyncMock()
    container.run.side_effect = _run_side_effect

    state = {
        **_BASE_STATE,
        "repo_path": str(tmp_path),
        "detected_package_manager": "npm",
        "package_manager_version": "latest",
        "docker_image": "node:20-alpine",
    }
    result = await install_deps(state, _config(container=container))

    assert result["has_lock_file"] is True
    assert container.run.await_count == 2
    fallback_command = container.run.call_args_list[1].kwargs["command"]
    assert "--package-lock-only" in fallback_command


@pytest.mark.asyncio
async def test_install_deps_pnpm_falls_back_to_lockfile_only(tmp_path):
    lock_path = tmp_path / "pnpm-lock.yaml"

    def _run_side_effect(*, image, command, volume, run_as_root):
        if "--lockfile-only" in command:
            lock_path.write_text("lockfileVersion: '9.0'\n")
        return (0, "", "")

    container = AsyncMock()
    container.run.side_effect = _run_side_effect

    state = {
        **_BASE_STATE,
        "repo_path": str(tmp_path),
        "detected_package_manager": "pnpm",
        "package_manager_version": "9",
        "docker_image": "node:20-alpine",
    }
    result = await install_deps(state, _config(container=container))

    assert result["has_lock_file"] is True
    assert container.run.await_count == 2
    fallback_command = container.run.call_args_list[1].kwargs["command"]
    assert "--lockfile-only" in fallback_command
    assert "pnpm@9" in fallback_command


@pytest.mark.asyncio
async def test_install_deps_yarn_has_no_lockfile_only_fallback(tmp_path):
    """yarn has no clean cross-version lockfile-only flag (Classic vs Berry
    differ); install_deps must not attempt a second call for it."""
    container = AsyncMock()
    container.run.return_value = (0, "", "")

    state = {
        **_BASE_STATE,
        "repo_path": str(tmp_path),
        "detected_package_manager": "yarn",
        "package_manager_version": "latest",
        "docker_image": "node:20-alpine",
    }
    result = await install_deps(state, _config(container=container))

    assert result["has_lock_file"] is False
    assert container.run.await_count == 1


@pytest.mark.asyncio
async def test_install_deps_retries_on_peer_conflict(tmp_path):
    lock_path = tmp_path / "package-lock.json"
    calls: list[str] = []

    def _run_side_effect(*, image, command, volume, run_as_root):
        calls.append(command)
        if len(calls) == 1:
            return (1, "", "npm error ERESOLVE: could not resolve peer dependency")
        lock_path.write_text("{}")  # simulate the retried install succeeding
        return (0, "", "")

    container = AsyncMock()
    container.run.side_effect = _run_side_effect

    state = {
        **_BASE_STATE,
        "repo_path": str(tmp_path),
        "detected_package_manager": "npm",
        "package_manager_version": "latest",
        "docker_image": "node:20-alpine",
    }
    await install_deps(state, _config(container=container))

    # Retry succeeded and produced a lock file, so the lockfile-only fallback
    # is not triggered — call count stays at 2 (initial failure + retry).
    assert container.run.await_count == 2
    assert "--legacy-peer-deps" in calls[1]


@pytest.mark.asyncio
async def test_clone_repo_captures_commit_sha(tmp_path):
    container = AsyncMock()
    # first call: git clone (success); second call: git rev-parse HEAD
    container.run.side_effect = [(0, "", ""), (0, "abc123def\n", "")]

    state = {**_BASE_STATE, "job_id": "sha-job"}
    result = await clone_repo(state, _config(container=container))

    assert result.get("commit_sha") == "abc123def"
    assert "discovery_error" not in result


@pytest.mark.asyncio
async def test_clone_repo_sha_empty_when_rev_parse_fails(tmp_path):
    container = AsyncMock()
    container.run.side_effect = [(0, "", ""), (1, "", "fatal")]

    state = {**_BASE_STATE, "job_id": "sha-job2"}
    result = await clone_repo(state, _config(container=container))

    # clone succeeded, so no discovery_error; sha just unavailable
    assert result.get("commit_sha") == ""
    assert "discovery_error" not in result
