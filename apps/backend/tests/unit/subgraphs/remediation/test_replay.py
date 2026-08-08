from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

import pytest

from src.main_graph.subgraphs.remediation.deepagent.replay import (
    _git_apply,
    apply_group_changes,
    replay_and_verify_group,
)
from src.models.remediation import Remediation


class FakeContainer:
    def __init__(self, results):
        self._results = list(results)

    async def run(
        self, image, command, volume=None, run_as_root=False, secret_env=None
    ):
        return self._results.pop(0)


def _bump(target_dep="lodash", to_range="^4.17.21"):
    return Remediation(
        addresses=[target_dep],
        target_dep=target_dep,
        strategy="bump",
        from_range="^4.17.11",
        to_range=to_range,
    )


def _init_git_repo(path: str) -> None:
    """Initialize a git repo with a committed initial state."""
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "init",
        ],
        cwd=path,
        check=True,
    )


def _get_git_diff(path: str, filename: str) -> str:
    """Get the git diff for a file after modification."""
    result = subprocess.run(
        ["git", "-C", path, "diff", "HEAD", filename],
        capture_output=True,
        check=True,
    )
    return result.stdout.decode()


@pytest.mark.asyncio
async def test_apply_group_changes_bumps_declared_dependency(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"lodash": "^4.17.11"}})
    )
    ok = await apply_group_changes(str(tmp_path), [_bump()])
    assert ok is True
    pkg = json.loads((tmp_path / "package.json").read_text())
    assert pkg["dependencies"]["lodash"] == "^4.17.21"


@pytest.mark.asyncio
async def test_apply_group_changes_false_when_bump_target_missing(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {}}))
    ok = await apply_group_changes(str(tmp_path), [_bump()])
    assert ok is False


@pytest.mark.asyncio
async def test_replay_and_verify_group_runs_full_verification(monkeypatch):
    # Create a temp dir structure matching copy_repo's real contract: dst/repo
    mkdtemp_root = tempfile.mkdtemp(prefix="remediation-")
    work = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work)

    pkg_path = os.path.join(work, "package.json")
    with open(pkg_path, "w") as f:
        json.dump({"dependencies": {"lodash": "^4.17.11"}, "scripts": {}}, f)

    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.deepagent.replay.copy_repo",
        lambda src: work,
    )
    audit = json.dumps({"vulnerabilities": {}})
    container = FakeContainer([(0, "", ""), (0, audit, "")])

    result, kept_dir = await replay_and_verify_group(
        [_bump()], "/original/repo", container, "node:lts-alpine", "npm"
    )

    assert result.installed is True
    assert result.finding_resolved is True
    assert kept_dir is None

    # Verify the correct directory was cleaned up (the mkdtemp_root, not
    # something too broad) - this tests that replay_and_verify_group's cleanup
    # correctly removes the parent directory created by copy_repo
    assert not os.path.exists(mkdtemp_root)


@pytest.mark.asyncio
async def test_replay_and_verify_group_reports_apply_failure(monkeypatch):
    # Create a temp dir structure matching copy_repo's real contract: dst/repo
    mkdtemp_root = tempfile.mkdtemp(prefix="remediation-")
    work = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work)

    pkg_path = os.path.join(work, "package.json")
    with open(pkg_path, "w") as f:
        json.dump({"dependencies": {}}, f)

    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.deepagent.replay.copy_repo",
        lambda src: work,
    )
    result, kept_dir = await replay_and_verify_group(
        [_bump()], "/original/repo", FakeContainer([]), "node:lts-alpine", "npm"
    )
    assert result.installed is False
    assert "failed to apply" in result.logs_snippet
    assert kept_dir is None

    # Verify the correct directory was cleaned up (the mkdtemp_root, not
    # something too broad) - this tests that replay_and_verify_group's cleanup
    # correctly removes the parent directory created by copy_repo
    assert not os.path.exists(mkdtemp_root)


@pytest.mark.asyncio
async def test_replay_and_verify_group_keeps_workdir_when_requested(monkeypatch):
    mkdtemp_root = tempfile.mkdtemp(prefix="remediation-")
    work = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work)

    pkg_path = os.path.join(work, "package.json")
    with open(pkg_path, "w") as f:
        json.dump({"dependencies": {"lodash": "^4.17.11"}, "scripts": {}}, f)

    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.deepagent.replay.copy_repo",
        lambda src: work,
    )
    audit = json.dumps({"vulnerabilities": {}})
    container = FakeContainer([(0, "", ""), (0, audit, "")])

    result, kept_dir = await replay_and_verify_group(
        [_bump()],
        "/original/repo",
        container,
        "node:lts-alpine",
        "npm",
        keep_workdir=True,
    )

    assert result.installed is True
    assert kept_dir == work
    # keep_workdir=True means the caller owns cleanup now.
    assert os.path.exists(mkdtemp_root)

    shutil.rmtree(mkdtemp_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_replay_and_verify_group_cleans_up_on_apply_failure_even_if_keep_requested(
    monkeypatch,
):
    mkdtemp_root = tempfile.mkdtemp(prefix="remediation-")
    work = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work)

    pkg_path = os.path.join(work, "package.json")
    with open(pkg_path, "w") as f:
        json.dump({"dependencies": {}}, f)

    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.deepagent.replay.copy_repo",
        lambda src: work,
    )
    result, kept_dir = await replay_and_verify_group(
        [_bump()],
        "/original/repo",
        FakeContainer([]),
        "node:lts-alpine",
        "npm",
        keep_workdir=True,
    )
    assert result.installed is False
    assert kept_dir is None
    assert not os.path.exists(mkdtemp_root)


@pytest.mark.asyncio
async def test_apply_group_changes_with_replace_strategy():
    """Test that strategy='replace' members are applied correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_path = f"{tmpdir}/package.json"
        with open(pkg_path, "w") as f:
            json.dump({"dependencies": {"old-lib": "^1.0.0"}}, f)

        replace_member = Remediation(
            addresses=["old-lib"],
            target_dep="old-lib",
            strategy="replace",
            replacement_dep="new-lib",
            replacement_range="^2.0.0",
        )
        ok = await apply_group_changes(tmpdir, [replace_member])
        assert ok is True
        with open(pkg_path) as f:
            pkg = json.load(f)
        assert "old-lib" not in pkg["dependencies"]
        assert pkg["dependencies"]["new-lib"] == "^2.0.0"


@pytest.mark.asyncio
async def test_apply_group_changes_with_bump_and_patch():
    """Test bump_with_codemod: both to_range and patch applied together."""
    with tempfile.TemporaryDirectory() as work_dir:
        # Create initial files
        pkg_path = f"{work_dir}/package.json"
        with open(pkg_path, "w") as f:
            json.dump({"dependencies": {"dep": "^1.0.0"}}, f)
        with open(f"{work_dir}/test.txt", "w") as f:
            f.write("old content")

        # Initialize git repo
        _init_git_repo(work_dir)

        # Create a valid patch for test.txt
        with open(f"{work_dir}/test.txt", "w") as f:
            f.write("new content")
        patch = _get_git_diff(work_dir, "test.txt")

        # Reset the file
        subprocess.run(["git", "-C", work_dir, "checkout", "test.txt"], check=True)

        member = Remediation(
            addresses=["dep"],
            target_dep="dep",
            strategy="bump_with_codemod",
            to_range="^2.0.0",
            patch=patch,
        )
        ok = await apply_group_changes(work_dir, [member])
        assert ok is True

        # Verify both bump and patch applied
        with open(pkg_path) as f:
            pkg = json.load(f)
        assert pkg["dependencies"]["dep"] == "^2.0.0"
        with open(f"{work_dir}/test.txt") as f:
            assert f.read() == "new content"


@pytest.mark.asyncio
async def test_git_apply_with_valid_patch_on_real_repo():
    """Test that _git_apply works with a real git repo and valid patch."""
    with tempfile.TemporaryDirectory() as work_dir:
        # Create a git repo with an initial file
        with open(f"{work_dir}/file.txt", "w") as f:
            f.write("line 1\nline 2\n")
        _init_git_repo(work_dir)

        # Make a change and capture the patch
        with open(f"{work_dir}/file.txt", "w") as f:
            f.write("line 1\nmodified line 2\n")
        patch = _get_git_diff(work_dir, "file.txt")

        # Reset the file to original state
        subprocess.run(["git", "-C", work_dir, "checkout", "file.txt"], check=True)

        # Verify file is back to original
        with open(f"{work_dir}/file.txt") as f:
            assert f.read() == "line 1\nline 2\n"

        # Apply the patch via _git_apply
        result = await _git_apply(work_dir, patch)
        assert result is True
        with open(f"{work_dir}/file.txt") as f:
            assert f.read() == "line 1\nmodified line 2\n"


@pytest.mark.asyncio
async def test_git_apply_returns_false_on_conflict():
    """Test that _git_apply returns False when patch doesn't apply cleanly."""
    with tempfile.TemporaryDirectory() as work_dir:
        # Create a git repo
        with open(f"{work_dir}/file.txt", "w") as f:
            f.write("line 1\nline 2\n")
        _init_git_repo(work_dir)

        # Create a patch that modifies line 2
        with open(f"{work_dir}/file.txt", "w") as f:
            f.write("line 1\nmodified line 2\n")
        original_patch = _get_git_diff(work_dir, "file.txt")

        # Reset to original
        subprocess.run(["git", "-C", work_dir, "checkout", "file.txt"], check=True)

        # Now change the file to something different
        with open(f"{work_dir}/file.txt", "w") as f:
            f.write("line 1\ncompletely different\n")

        # The original patch won't apply because context doesn't match
        result = await _git_apply(work_dir, original_patch)
        assert result is False
