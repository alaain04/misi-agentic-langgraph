from langgraph.types import Send

from src.main_graph.nodes.skill_dispatcher import skill_dispatcher
from src.models.hypothesis import Hypothesis
from src.models.investigation_plan import InvestigationPlan, SkillAssignment


def _make_state(skill_ids: list[str], dep: str = "lodash") -> dict:
    h = Hypothesis(id="h1", dep_name=dep, statement="test", risk_theme="vulnerability", rationale="r", skills=skill_ids)
    plan = InvestigationPlan(
        concern="security",
        hypotheses=[h],
        skill_plan=[SkillAssignment(dep_name=dep, hypothesis_id="h1", skill_id=sid) for sid in skill_ids],
        rationale="test",
    )
    return {
        "investigation_plan": plan,
        "repo_path": "/tmp/repo",
        "sbom_cyclonedx": {},
        "concern": "security",
    }


def test_dispatcher_emits_send_per_assignment():
    state = _make_state(["VulnerabilitySkill", "LicenseSkill"])
    sends = skill_dispatcher(state)
    assert len(sends) == 2
    assert all(isinstance(s, Send) for s in sends)
    assert all(s.node == "skill_executor" for s in sends)


def test_dispatcher_skips_unknown_skill():
    state = _make_state(["VulnerabilitySkill", "NonExistentSkill"])
    sends = skill_dispatcher(state)
    assert len(sends) == 1
    assert sends[0].arg["current_skill_id"] == "VulnerabilitySkill"


def test_dispatcher_skips_skill_when_required_inputs_missing():
    # VulnerabilitySkill requires repo_path
    state = _make_state(["VulnerabilitySkill"])
    state["repo_path"] = None
    sends = skill_dispatcher(state)
    assert len(sends) == 0
