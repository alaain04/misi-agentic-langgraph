from src.main_graph.skills.registry import SKILL_REGISTRY, SKILL_DESCRIPTIONS

EXPECTED_SKILL_IDS = {
    "VulnerabilitySkill",
    "MaintainerTrustSkill",
    "SupplyChainSkill",
    "LicenseSkill",
    "ReachabilitySkill",
    "BlastRadiusSkill",
    "ReleaseAnomalySkill",
    "EcosystemSkill",
}


def test_all_skills_registered():
    assert set(SKILL_REGISTRY.keys()) == EXPECTED_SKILL_IDS


def test_skill_descriptions_match_registry():
    assert set(SKILL_DESCRIPTIONS.keys()) == EXPECTED_SKILL_IDS


def test_each_skill_has_required_attributes():
    for skill_id, skill in SKILL_REGISTRY.items():
        assert skill.id == skill_id
        assert skill.name
        assert skill.description
        assert skill.trigger_conditions
        assert skill.required_inputs is not None
        assert skill.evidence_kinds


async def test_stub_execute_returns_empty_list():
    from src.main_graph.skills.base import SkillContext
    ctx = SkillContext(
        dep_name="lodash",
        hypothesis_id="h1",
        hypothesis="lodash may be risky",
        sbom={},
        concern="security",
    )
    for skill in SKILL_REGISTRY.values():
        result = await skill.execute(ctx)
        assert isinstance(result, list)
