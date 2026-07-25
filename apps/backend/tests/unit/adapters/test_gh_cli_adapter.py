import pytest

from src.main_graph.adapters.gh_cli_adapter import GhCliAdapter


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


@pytest.mark.asyncio
async def test_open_pr_raises_on_git_failure(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return FakeProc(1, err=b"branch exists")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    with pytest.raises(RuntimeError, match="branch exists"):
        await GhCliAdapter().open_pr("/w", "b", "t", "b")
