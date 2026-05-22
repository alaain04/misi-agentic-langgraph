from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.maintainer_trust import MaintainerTrustSkill
from src.main_graph.skills.release_anomaly import ReleaseAnomalySkill


def _make_ctx(dep="lodash"):
    return SkillContext(
        dep_name=dep,
        hypothesis_id="h1",
        hypothesis=f"{dep} may be abandoned",
        sbom={},
        concern="maintainer trust",
        services={"mcp_client": AsyncMock()},
    )


@pytest.mark.asyncio
async def test_maintainer_trust_active_project():
    ctx = _make_ctx()
    skill = MaintainerTrustSkill()

    with patch("src.main_graph.skills.maintainer_trust._fetch_repo_data") as mock:
        mock.return_value = {
            "commits_last_90_days": 45,
            "open_issues": 12,
            "closed_issues_last_90_days": 30,
            "contributors": 8,
        }
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.kind == "maintainer_signal"
    assert ev.supports_hypothesis is False  # active project → not abandoned


@pytest.mark.asyncio
async def test_maintainer_trust_abandoned_project():
    ctx = _make_ctx()
    skill = MaintainerTrustSkill()

    with patch("src.main_graph.skills.maintainer_trust._fetch_repo_data") as mock:
        mock.return_value = {
            "commits_last_90_days": 0,
            "open_issues": 150,
            "closed_issues_last_90_days": 0,
            "contributors": 1,
        }
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].supports_hypothesis is True
    assert evidence[0].severity in ("high", "medium")


@pytest.mark.asyncio
async def test_release_anomaly_suspicious_pattern():
    ctx = _make_ctx()
    skill = ReleaseAnomalySkill()

    with patch("src.main_graph.skills.release_anomaly._fetch_releases") as mock:
        mock.return_value = [
            {"version": "1.0.0", "days_since_previous": 2},
            {"version": "1.0.1", "days_since_previous": 1},
            {"version": "1.0.2", "days_since_previous": 1},
        ]
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].kind == "release_anomaly"


@pytest.mark.asyncio
async def test_release_anomaly_normal_pattern():
    ctx = _make_ctx()
    skill = ReleaseAnomalySkill()

    with patch("src.main_graph.skills.release_anomaly._fetch_releases") as mock:
        mock.return_value = [
            {"version": "1.0.0", "days_since_previous": 90},
            {"version": "2.0.0", "days_since_previous": 180},
        ]
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].supports_hypothesis is False
