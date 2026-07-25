import json
import os
import subprocess

import pytest

from src.main_graph.subgraphs.remediation.workspace import (
    apply_bump,
    copy_repo,
    pm_commands,
    working_copy_diff,
)


@pytest.fixture
def git_repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    (d / "package.json").write_text(json.dumps(
        {"name": "x", "dependencies": {"lodash": "^4.17.11"},
         "devDependencies": {"jest": "^29.0.0"}}))
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=d, check=True)
    return str(d)


def test_copy_repo_is_independent(git_repo):
    copy = copy_repo(git_repo)
    assert copy != git_repo
    assert os.path.isfile(os.path.join(copy, "package.json"))
    assert os.path.isdir(os.path.join(copy, ".git"))
    # mutating the copy does not touch the source
    apply_bump(copy, "lodash", "^4.17.21")
    src_pkg = json.load(open(os.path.join(git_repo, "package.json")))
    assert src_pkg["dependencies"]["lodash"] == "^4.17.11"


def test_apply_bump_dependencies(git_repo):
    assert apply_bump(git_repo, "lodash", "^4.17.21") is True
    pkg = json.load(open(os.path.join(git_repo, "package.json")))
    assert pkg["dependencies"]["lodash"] == "^4.17.21"


def test_apply_bump_devdependencies(git_repo):
    assert apply_bump(git_repo, "jest", "^29.7.0") is True
    pkg = json.load(open(os.path.join(git_repo, "package.json")))
    assert pkg["devDependencies"]["jest"] == "^29.7.0"


def test_apply_bump_undeclared_returns_false(git_repo):
    assert apply_bump(git_repo, "not-there", "^1.0.0") is False


@pytest.mark.asyncio
async def test_working_copy_diff_reflects_change(git_repo):
    apply_bump(git_repo, "lodash", "^4.17.21")
    diff = await working_copy_diff(git_repo)
    assert "package.json" in diff and "4.17.21" in diff


def test_pm_commands_variants():
    assert pm_commands("pnpm")["install"] == "pnpm install --no-frozen-lockfile"
    assert pm_commands("yarn")["build"] == "yarn build"
    assert pm_commands("weird")["test"] == "npm test"  # fallback
