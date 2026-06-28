import json
from unittest.mock import AsyncMock, patch

from src.models.investigation_plan import InvestigationPlan
from src.main_graph.nodes.investigation_planner_service import (
    _run_planner,
    _select_deps,
    _get_direct_dep_names,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_component(name: str, bom_ref: str | None = None) -> dict:
    c = {"name": name}
    if bom_ref:
        c["bom-ref"] = bom_ref
    return c


def _make_large_sbom(n_transitive: int = 35) -> dict:
    direct_refs = ["pkg:direct-a", "pkg:direct-b", "pkg:direct-c"]
    transitive_refs = [f"pkg:trans-{i}" for i in range(n_transitive)]
    components = (
        [{"name": f"direct-{chr(97+i)}", "bom-ref": r} for i, r in enumerate(direct_refs)]
        + [{"name": f"trans-{i}", "bom-ref": r} for i, r in enumerate(transitive_refs)]
    )
    return {
        "metadata": {"component": {"bom-ref": "pkg:root"}},
        "components": components,
        "dependencies": [{"ref": "pkg:root", "dependsOn": direct_refs}],
    }


_SMALL_SBOM = {
    "metadata": {"component": {"bom-ref": "pkg:root"}},
    "components": [
        {"name": "react", "bom-ref": "pkg:react"},
        {"name": "lodash", "bom-ref": "pkg:lodash"},
        {"name": "axios", "bom-ref": "pkg:axios"},
    ],
    "dependencies": [{"ref": "pkg:root", "dependsOn": ["pkg:react"]}],
}


# ── _get_direct_dep_names ─────────────────────────────────────────────────────

def test_get_direct_dep_names_returns_names_from_dependencies_section():
    names = _get_direct_dep_names(_SMALL_SBOM)
    assert names == {"react"}


def test_get_direct_dep_names_no_metadata_returns_empty():
    sbom = {"components": [{"name": "x", "bom-ref": "pkg:x"}]}
    assert _get_direct_dep_names(sbom) == set()


def test_get_direct_dep_names_no_matching_root_returns_empty():
    sbom = {
        "metadata": {"component": {"bom-ref": "pkg:root"}},
        "components": [{"name": "x", "bom-ref": "pkg:x"}],
        "dependencies": [{"ref": "pkg:other", "dependsOn": ["pkg:x"]}],
    }
    assert _get_direct_dep_names(sbom) == set()


# ── _select_deps ──────────────────────────────────────────────────────────────

async def test_select_deps_small_sbom_returns_all_without_llm_call():
    mock_llm = AsyncMock()
    with patch("src.main_graph.nodes.investigation_planner_service._llm", mock_llm):
        result = await _select_deps("security audit", _SMALL_SBOM["components"], _SMALL_SBOM)

    assert len(result) == 3
    mock_llm.ainvoke.assert_not_called()


async def test_select_deps_large_sbom_always_includes_all_direct_deps():
    sbom = _make_large_sbom(n_transitive=35)
    direct_names = {"direct-a", "direct-b", "direct-c"}
    ranked_response = [f"trans-{i}" for i in range(20)]

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AsyncMock(content=json.dumps(ranked_response))

    with patch("src.main_graph.nodes.investigation_planner_service._llm", mock_llm):
        result = await _select_deps("prototype pollution", sbom["components"], sbom)

    result_names = {c["name"] for c in result}
    assert direct_names.issubset(result_names)


async def test_select_deps_large_sbom_limits_transitives_to_llm_ranked():
    sbom = _make_large_sbom(n_transitive=35)
    top_transitives = ["trans-5", "trans-12", "trans-3"]
    ranked_response = top_transitives + [f"trans-{i}" for i in range(20) if i not in (5, 12, 3)]

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AsyncMock(content=json.dumps(ranked_response))

    with patch("src.main_graph.nodes.investigation_planner_service._llm", mock_llm):
        result = await _select_deps("supply chain", sbom["components"], sbom)

    result_names = {c["name"] for c in result}
    # top ranked transitives must be in the result
    assert "trans-5" in result_names
    assert "trans-12" in result_names
    assert "trans-3" in result_names
    # total should be bounded (3 direct + max 20 transitive)
    assert len(result) <= 23


async def test_select_deps_no_dependencies_section_ranks_all_as_transitives():
    sbom = {
        "components": [{"name": f"pkg-{i}"} for i in range(35)],
    }
    ranked_response = [f"pkg-{i}" for i in range(20)]

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AsyncMock(content=json.dumps(ranked_response))

    with patch("src.main_graph.nodes.investigation_planner_service._llm", mock_llm):
        result = await _select_deps("license risk", sbom["components"], sbom)

    assert len(result) <= 20
    mock_llm.ainvoke.assert_called_once()


# ── _run_planner ──────────────────────────────────────────────────────────────

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


async def test_run_planner_large_sbom_uses_selector_before_planning():
    sbom = _make_large_sbom(n_transitive=35)
    state = {
        "concern": "supply chain attack",
        "discovery_summary": "Node app",
        "sbom_cyclonedx": sbom,
        "job_id": "job-2",
    }
    selector_response = ["trans-1", "trans-7"]
    planner_response = {
        "hypotheses": [{
            "id": "h1",
            "dep_name": "trans-1",
            "statement": "trans-1 may be compromised",
            "risk_theme": "supply_chain",
            "rationale": "suspicious",
            "skills": ["SupplyChainSkill"],
        }],
        "rationale": "supply chain focus",
        "dep_filter": None,
    }

    call_count = 0
    async def fake_invoke(messages):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return AsyncMock(content=json.dumps(selector_response))
        return AsyncMock(content=json.dumps(planner_response))

    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = fake_invoke

    with patch("src.main_graph.nodes.investigation_planner_service._llm", mock_llm):
        plan = await _run_planner(state)

    assert call_count == 2  # selector + planner
    assert isinstance(plan, InvestigationPlan)


# ── investigation_planner_service ────────────────────────────────────────────────

_PLAN_LLM_RESPONSE = {
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

_PLANNER_STATE = {
    "job_id": "job-1",
    "concern": "security audit",
    "discovery_summary": "React app with 50 deps",
    "sbom_cyclonedx": {"components": [{"name": "lodash"}]},
    "messages": [],
}


async def test_planner_service_pushes_assistant_and_human_messages_on_approve():
    from src.main_graph.constants import INVESTIGATION_PLANNER
    from src.main_graph.nodes.investigation_planner_service import investigation_planner_service

    dao = AsyncMock()

    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = [
        AsyncMock(content=json.dumps(_PLAN_LLM_RESPONSE)),  # _run_planner
        AsyncMock(content="approve"),                        # _classify_intent
    ]

    with (
        patch("src.main_graph.nodes.investigation_planner_service._llm", mock_llm),
        patch("src.main_graph.nodes.investigation_planner_service.interrupt", return_value="looks good"),
    ):
        result = await investigation_planner_service(_PLANNER_STATE, dao)

    assert "investigation_plan" in result

    calls = dao.push_artifact_message.await_args_list
    assert len(calls) == 2

    assert calls[0].args[0] == "job-1"
    assert calls[0].args[1] == INVESTIGATION_PLANNER
    assert calls[0].args[2]["role"] == "assistant"
    assert "content" in calls[0].args[2]
    assert "created_at" in calls[0].args[2]

    assert calls[1].args[1] == INVESTIGATION_PLANNER
    assert calls[1].args[2]["role"] == "human"
    assert calls[1].args[2]["content"] == "looks good"
    assert calls[1].args[2]["action"] == "approve"

    dao.update_artifact_data.assert_awaited_once()
    data_call = dao.update_artifact_data.await_args_list[0]
    assert data_call.args[1] == INVESTIGATION_PLANNER
    assert "plan" in data_call.args[2]["data"]


async def test_planner_service_pushes_four_messages_on_change_then_approve():
    from src.main_graph.constants import INVESTIGATION_PLANNER
    from src.main_graph.nodes.investigation_planner_service import investigation_planner_service

    dao = AsyncMock()

    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = [
        AsyncMock(content=json.dumps(_PLAN_LLM_RESPONSE)),  # _run_planner (initial)
        AsyncMock(content="change"),                         # _classify_intent (first response)
        AsyncMock(content=json.dumps(_PLAN_LLM_RESPONSE)),  # _run_planner (re-plan)
        AsyncMock(content="approve"),                        # _classify_intent (second response)
    ]

    with (
        patch("src.main_graph.nodes.investigation_planner_service._llm", mock_llm),
        patch(
            "src.main_graph.nodes.investigation_planner_service.interrupt",
            side_effect=["focus on licenses", "ok proceed"],
        ),
    ):
        await investigation_planner_service(_PLANNER_STATE, dao)

    calls = dao.push_artifact_message.await_args_list
    assert len(calls) == 4
    assert calls[0].args[2]["role"] == "assistant"
    assert calls[1].args[2]["role"] == "human"
    assert calls[1].args[2]["action"] == "change"
    assert calls[1].args[2]["content"] == "focus on licenses"
    assert calls[2].args[2]["role"] == "assistant"
    assert calls[3].args[2]["role"] == "human"
    assert calls[3].args[2]["action"] == "approve"

    assert dao.update_artifact_data.await_count == 2
