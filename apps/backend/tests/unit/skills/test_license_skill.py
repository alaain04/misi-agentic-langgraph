import json
from unittest.mock import AsyncMock

import pytest

from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.license import LicenseSkill
from src.domain.ports.container_run_port import ContainerRunPort


def _make_ctx(repo_path="/tmp/repo"):
    return SkillContext(
        dep_name="lodash",
        hypothesis_id="h1",
        hypothesis="lodash may have license violations",
        sbom={},
        concern="license compliance",
        repo_path=repo_path,
        services={"container": AsyncMock(spec=ContainerRunPort)},
    )


@pytest.mark.asyncio
async def test_license_skill_produces_evidence():
    trivy_output = {
        "Results": [{"Licenses": [
            {"PkgName": "lodash", "Name": "GPL-3.0", "Category": "restricted"},
        ]}]
    }
    ctx = _make_ctx()
    ctx.services["container"].run.return_value = (0, json.dumps(trivy_output), "")

    skill = LicenseSkill()
    evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.kind == "license_signal"
    assert ev.dep_name == "lodash"
    assert ev.skill_id == "LicenseSkill"
    assert ev.severity in ("high", "medium", "low")
    assert ev.supports_hypothesis is True
    assert 0.0 <= ev.confidence <= 1.0


@pytest.mark.asyncio
async def test_license_skill_no_repo_path_returns_empty():
    ctx = _make_ctx(repo_path=None)
    skill = LicenseSkill()
    evidence = await skill.execute(ctx)
    assert evidence == []


@pytest.mark.asyncio
async def test_license_skill_permissive_license_low_severity():
    trivy_output = {
        "Results": [{"Licenses": [
            {"PkgName": "express", "Name": "MIT", "Category": "permissive"},
        ]}]
    }
    ctx = _make_ctx()
    ctx.dep_name = "express"
    ctx.services["container"].run.return_value = (0, json.dumps(trivy_output), "")

    skill = LicenseSkill()
    evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].severity == "low"
    assert evidence[0].supports_hypothesis is False
