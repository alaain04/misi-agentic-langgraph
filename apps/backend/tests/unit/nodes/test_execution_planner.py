# tests/unit/nodes/test_execution_planner.py
import pytest

from src.main_graph.nodes.execution_planner import execution_planner

_SBOM = {
    "components": [
        {"name": "lodash", "version": "4.17.21"},
        {"name": "express", "version": "4.18.0"},
    ]
}


def _make_state(**kwargs) -> dict:
    base = {
        "job_id": "j1",
        "concern": "security",
        "sbom_cyclonedx": _SBOM,
        "plan": {"subgraphs": ["vulnerabilities", "registry"], "dep_filter": None},
        "messages": [],
        "subgraph_results": [],
    }
    base.update(kwargs)
    return base


def test_execution_planner_generates_sbom_level_entry():
    state = _make_state()
    result = execution_planner(state)

    stage0 = result["execution_stages"][0]
    sbom_entries = [e for e in stage0 if e["subgraph"] == "vulnerabilities"]
    assert len(sbom_entries) == 1
    assert sbom_entries[0]["dep_name"] is None


def test_execution_planner_generates_per_dep_entries():
    state = _make_state()
    result = execution_planner(state)

    stage0 = result["execution_stages"][0]
    registry_entries = [e for e in stage0 if e["subgraph"] == "registry"]
    dep_names = {e["dep_name"] for e in registry_entries}
    assert dep_names == {"lodash", "express"}


def test_execution_planner_respects_dep_filter():
    state = _make_state(plan={"subgraphs": ["registry"], "dep_filter": ["lodash"]})
    result = execution_planner(state)

    stage0 = result["execution_stages"][0]
    registry_entries = [e for e in stage0 if e["subgraph"] == "registry"]
    assert len(registry_entries) == 1
    assert registry_entries[0]["dep_name"] == "lodash"


def test_execution_planner_skips_if_stages_exist():
    state = _make_state(execution_stages=[[{"subgraph": "vulnerabilities", "dep_name": None}]])
    result = execution_planner(state)
    assert result == {}


def test_execution_planner_sets_stage_index_zero():
    state = _make_state()
    result = execution_planner(state)
    assert result["current_stage_index"] == 0
