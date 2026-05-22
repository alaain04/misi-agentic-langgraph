from src.main_graph.plan import Plan
from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState


def test_plan_requires_subgraphs():
    p: Plan = {"subgraphs": ["vulnerabilities"], "dep_filter": None}
    assert p["subgraphs"] == ["vulnerabilities"]
    assert p["dep_filter"] is None


def test_plan_dep_filter_optional():
    p: Plan = {"subgraphs": ["registry"]}
    assert "dep_filter" not in p


def test_analysis_state_accepts_dependency_name():
    s: AnalysisState = {
        "sbom_cyclonedx": {},
        "discovery_summary": "ok",
        "concern": "security",
        "dependency_name": "lodash",
    }
    assert s["dependency_name"] == "lodash"
