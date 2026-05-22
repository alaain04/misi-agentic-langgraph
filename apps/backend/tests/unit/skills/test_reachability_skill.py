from unittest.mock import patch

from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.reachability import ReachabilitySkill


def _make_ctx(repo_path="/tmp/repo"):
    return SkillContext(
        dep_name="lodash",
        hypothesis_id="h1",
        hypothesis="lodash may be unreachable",
        sbom={},
        concern="impact",
        repo_path=repo_path,
        services={},
    )


async def test_reachability_skill_dep_is_used():
    ctx = _make_ctx()
    skill = ReachabilitySkill()

    with patch("src.main_graph.skills.reachability.find_usages") as mock_find:
        mock_find.invoke.return_value = '["src/utils.ts:import lodash"]'
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.kind == "reachability_signal"
    assert ev.supports_hypothesis is False  # dep IS reachable → does not support "unreachable" hypothesis


async def test_reachability_skill_dep_not_used():
    ctx = _make_ctx()
    skill = ReachabilitySkill()

    with patch("src.main_graph.skills.reachability.find_usages") as mock_find:
        mock_find.invoke.return_value = "[]"
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.kind == "reachability_signal"
    assert ev.supports_hypothesis is True  # dep NOT reachable
    assert "not found" in ev.signal.lower() or "unreachable" in ev.signal.lower()


async def test_reachability_skill_no_repo_path():
    ctx = _make_ctx(repo_path=None)
    skill = ReachabilitySkill()
    assert await skill.execute(ctx) == []
