from unittest.mock import AsyncMock, patch

from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.supply_chain import SupplyChainSkill
from src.main_graph.skills.ecosystem import EcosystemSkill


def _make_ctx(dep="lodash"):
    return SkillContext(
        dep_name=dep,
        hypothesis_id="h1",
        hypothesis=f"{dep} may be a supply chain risk",
        sbom={},
        concern="supply chain",
        services={"mcp_client": AsyncMock()},
    )


async def test_supply_chain_suspicious_package():
    ctx = _make_ctx()
    skill = SupplyChainSkill()

    with patch("src.main_graph.skills.supply_chain._fetch_registry_metadata") as mock:
        mock.return_value = {
            "has_install_scripts": True,
            "owner_changed_recently": True,
            "name_similarity_score": 0.95,
        }
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].kind == "supply_chain_signal"
    assert evidence[0].supports_hypothesis is True


async def test_ecosystem_healthy_package():
    ctx = _make_ctx()
    skill = EcosystemSkill()

    with patch("src.main_graph.skills.ecosystem._fetch_ecosystem_data") as mock:
        mock.return_value = {
            "weekly_downloads": 10_000_000,
            "dependents": 50_000,
            "stars": 58_000,
        }
        evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].kind == "ecosystem_signal"
    assert evidence[0].supports_hypothesis is False  # healthy → does not support risk hypothesis
