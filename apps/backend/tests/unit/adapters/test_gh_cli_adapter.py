import subprocess

import pytest

from src.main_graph.adapters.gh_cli_adapter import (
    _COMMIT_EXCLUDE_PATHSPECS,
    GhCliAdapter,
)


class FakeProc:
    def __init__(self, rc, out=b"", err=b""):
        self.returncode = rc
        self._out, self._err = out, err

    async def communicate(self):
        return self._out, self._err


@pytest.mark.asyncio
async def test_open_pr_runs_steps_and_returns_url(monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if args[0] == "gh":
            return FakeProc(0, out=b"https://github.com/o/r/pull/7\n")
        return FakeProc(0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    url = await GhCliAdapter().open_pr("/w", "remediation/j1", "Fix deps", "body")
    assert url == "https://github.com/o/r/pull/7"
    programs = [c[0] for c in calls]
    assert programs.count("git") >= 4 and programs[-1] == "gh"
    add_call = next(c for c in calls if c[:2] == ("git", "add"))
    for pathspec in _COMMIT_EXCLUDE_PATHSPECS:
        assert pathspec in add_call


def test_commit_exclude_pathspecs_keep_node_modules_and_codegraph_unstaged(tmp_path):
    """Regression: a real remediation PR shipped node_modules because a
    verification step re-installed dependencies into the same work_dir that
    later gets committed -- copy_repo's copytree exclusion never runs again
    at that point, so it can't protect against it. The git-add boundary in
    open_pr is the actual guarantee; assert it against a real git repo
    rather than mocked subprocess calls."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    (tmp_path / "package.json").write_text("{}")
    nm = tmp_path / "node_modules" / "lodash"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("x")
    nested_nm = tmp_path / "packages" / "foo" / "node_modules"
    nested_nm.mkdir(parents=True)
    (nested_nm / "bar.js").write_text("x")
    cg = tmp_path / ".codegraph"
    cg.mkdir()
    (cg / "index.db").write_text("x")

    subprocess.run(
        ["git", "add", "-A", "--", ".", *_COMMIT_EXCLUDE_PATHSPECS],
        cwd=tmp_path,
        check=True,
    )
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    assert staged == ["package.json"]


@pytest.mark.asyncio
async def test_open_pr_raises_on_git_failure(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return FakeProc(1, err=b"branch exists")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    with pytest.raises(RuntimeError, match="branch exists"):
        await GhCliAdapter().open_pr("/w", "b", "t", "b")
