import json
from unittest.mock import AsyncMock, patch

from src.models.investigation_plan import InvestigationPlan
from src.main_graph.nodes.investigation_planner_service import _run_planner


async def test_run_planner_returns_investigation_plan():
    state = {
        "concern": "security audit",
        "discovery_summary": "React app with 50 deps",
        "sbom_cyclonedx": {"components": [{"name": "lodash"}, {"name": "express"}]},
        "job_id": "job-1",
    }
    llm_response = {
        "hypotheses": [{
            "id": "h1",
            "dep_name": "lodash",
            "statement": "lodash may expose prototype pollution",
            "risk_theme": "vulnerability",
            "rationale": "known CVEs",
            "skills": ["VulnerabilitySkill"],
        }],
        "rationale": "security focus",
        "dep_filter": None,
    }

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AsyncMock(content=json.dumps(llm_response))

    with patch("src.main_graph.nodes.investigation_planner_service._llm", mock_llm):
        plan = await _run_planner(state)

    assert isinstance(plan, InvestigationPlan)
    assert len(plan.hypotheses) == 1
    assert plan.hypotheses[0].dep_name == "lodash"
    assert len(plan.skill_plan) == 1
    assert plan.skill_plan[0].skill_id == "VulnerabilitySkill"
    assert plan.rationale == "security focus"
