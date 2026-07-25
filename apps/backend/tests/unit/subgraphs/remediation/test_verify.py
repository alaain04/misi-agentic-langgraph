import json

import pytest

from src.main_graph.subgraphs.remediation.verify import verify_working_copy


class FakeContainer:
    """Returns queued (rc, stdout, stderr) per run() call, in order."""
    def __init__(self, results):
        self._results = list(results)
        self.commands = []

    async def run(
        self, image, command, volume=None, run_as_root=False, secret_env=None
    ):
        self.commands.append(command)
        return self._results.pop(0)


@pytest.fixture
def work_dir(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps(
        {"name": "x", "scripts": {"build": "tsc", "test": "jest"},
         "dependencies": {"lodash": "^4.17.21"}}))
    return str(tmp_path)


@pytest.mark.asyncio
async def test_all_green_vuln_resolved(work_dir):
    audit = json.dumps({"vulnerabilities": {}})
    c = FakeContainer([(0, "", ""), (0, "", ""), (0, "", ""), (0, audit, "")])
    v = await verify_working_copy(work_dir, c, "node:lts-alpine", "npm", ["lodash"])
    assert v.installed and v.built and v.tested and v.finding_resolved is True


@pytest.mark.asyncio
async def test_install_failure_short_circuits(work_dir):
    c = FakeContainer([(1, "", "ENOENT")])
    v = await verify_working_copy(work_dir, c, "node:lts-alpine", "npm", ["lodash"])
    assert v.installed is False
    assert v.built is None and v.tested is None and v.finding_resolved is None
    assert "ENOENT" in v.logs_snippet
    assert len(c.commands) == 1  # stopped after install


@pytest.mark.asyncio
async def test_no_build_no_test_scripts(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "x", "dependencies": {"lodash": "^4.17.21"}})
    )
    audit = json.dumps({"vulnerabilities": {}})
    c = FakeContainer([(0, "", ""), (0, audit, "")])  # install, audit only
    v = await verify_working_copy(
        str(tmp_path), c, "node:lts-alpine", "npm", ["lodash"]
    )
    assert v.installed and v.built is None and v.tested is None
    assert v.finding_resolved is True


@pytest.mark.asyncio
async def test_placeholder_test_script_is_skipped(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({
            "name": "x",
            "scripts": {"test": 'echo "Error: no test specified" && exit 1'},
            "dependencies": {"lodash": "^4.17.21"},
        })
    )
    audit = json.dumps({"vulnerabilities": {}})
    c = FakeContainer([(0, "", ""), (0, audit, "")])
    v = await verify_working_copy(
        str(tmp_path), c, "node:lts-alpine", "npm", ["lodash"]
    )
    assert v.tested is None


@pytest.mark.asyncio
async def test_finding_not_resolved_when_still_vulnerable(work_dir):
    audit = json.dumps({"vulnerabilities": {"lodash": {"severity": "high"}}})
    c = FakeContainer([(0, "", ""), (0, "", ""), (0, "", ""), (0, audit, "")])
    v = await verify_working_copy(work_dir, c, "node:lts-alpine", "npm", ["lodash"])
    assert v.finding_resolved is False


@pytest.mark.asyncio
async def test_test_failure_marks_tested_false(work_dir):
    audit = json.dumps({"vulnerabilities": {}})
    c = FakeContainer([(0, "", ""), (0, "", ""), (1, "", "1 failing"), (0, audit, "")])
    v = await verify_working_copy(work_dir, c, "node:lts-alpine", "npm", ["lodash"])
    assert v.built is True and v.tested is False
