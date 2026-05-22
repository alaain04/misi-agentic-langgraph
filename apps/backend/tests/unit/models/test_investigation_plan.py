from src.models.hypothesis import Hypothesis
from src.models.investigation_plan import InvestigationPlan, SkillAssignment


def test_hypothesis_defaults():
    h = Hypothesis(
        id="h1",
        dep_name="lodash",
        statement="lodash may expose prototype pollution",
        risk_theme="vulnerability",
        rationale="lodash has known CVEs",
        skills=["VulnerabilitySkill"],
    )
    assert h.status == "open"
    assert h.confidence is None


def test_investigation_plan():
    plan = InvestigationPlan(
        concern="security audit",
        hypotheses=[
            Hypothesis(
                id="h1",
                dep_name="lodash",
                statement="lodash may expose prototype pollution",
                risk_theme="vulnerability",
                rationale="known CVEs",
                skills=["VulnerabilitySkill"],
            )
        ],
        skill_plan=[
            SkillAssignment(dep_name="lodash", hypothesis_id="h1", skill_id="VulnerabilitySkill")
        ],
        rationale="security focus given concern",
    )
    assert len(plan.hypotheses) == 1
    assert len(plan.skill_plan) == 1
    assert plan.dep_filter is None
