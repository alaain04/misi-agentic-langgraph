from __future__ import annotations

import json

import pytest

from src.main_graph.subgraphs.remediation.deepagent.replay import (
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
async def test_replay_and_verify_group_runs_full_verification(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"lodash": "^4.17.11"}, "scripts": {}})
    )
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.deepagent.replay.copy_repo",
        lambda src: str(tmp_path),
    )
    audit = json.dumps({"vulnerabilities": {}})
    container = FakeContainer([(0, "", ""), (0, audit, "")])

    result = await replay_and_verify_group(
        [_bump()], "/original/repo", container, "node:lts-alpine", "npm"
    )

    assert result.installed is True
    assert result.finding_resolved is True


@pytest.mark.asyncio
async def test_replay_and_verify_group_reports_apply_failure(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {}}))
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.deepagent.replay.copy_repo",
        lambda src: str(tmp_path),
    )
    result = await replay_and_verify_group(
        [_bump()], "/original/repo", FakeContainer([]), "node:lts-alpine", "npm"
    )
    assert result.installed is False
    assert "failed to apply" in result.logs_snippet
