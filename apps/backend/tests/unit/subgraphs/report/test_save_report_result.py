from src.main_graph.subgraphs.report.nodes.save_report_result import (
    _grounded_blast_radius,
    _group_enrichment_by_dep,
)
from src.models.conductor import ToolResult


def _tool_result(tool: str, args: dict, output: dict, error: str | None = None):
    return ToolResult(
        id="id",
        tool=tool,
        args=args,
        output=output,
        error=error,
        duration_ms=1,
    )


def test_group_enrichment_attributes_blast_radius_by_package_name():
    tool_results = [
        _tool_result(
            "blast_radius",
            {"package_name": "left-pad"},
            {"available": True, "affected_file_count": 2},
        ),
        _tool_result(
            "blast_radius",
            {"package_name": "axios"},
            {"available": True, "affected_file_count": 5},
        ),
    ]
    grouped = _group_enrichment_by_dep(tool_results, ["left-pad", "axios"])

    assert (
        grouped["by_dependency"]["left-pad"]["blast_radius"][0]["output"][
            "affected_file_count"
        ]
        == 2
    )
    assert (
        grouped["by_dependency"]["axios"]["blast_radius"][0]["output"][
            "affected_file_count"
        ]
        == 5
    )
    assert grouped["general"] == []


def test_group_enrichment_attributes_code_impact_by_package_name():
    tool_results = [
        _tool_result(
            "code_impact",
            {"package_name": "left-pad"},
            {"results": [{"file": "src/format.ts", "snippet": "leftPad(str, 10)"}]},
        ),
    ]
    grouped = _group_enrichment_by_dep(tool_results, ["left-pad", "axios"])

    assert grouped["by_dependency"]["left-pad"]["code_impact"][0]["output"] == {
        "results": [{"file": "src/format.ts", "snippet": "leftPad(str, 10)"}]
    }
    assert "code_impact" not in grouped["by_dependency"]["axios"]
    assert grouped["general"] == []


def test_group_enrichment_attributes_web_search_by_query_substring():
    tool_results = [
        _tool_result(
            "web_search",
            {"query": "left-pad alternatives npm"},
            {"results": [{"title": "use padStart instead"}]},
        )
    ]
    grouped = _group_enrichment_by_dep(tool_results, ["left-pad", "axios"])

    assert len(grouped["by_dependency"]["left-pad"]["web_search_hits"]) == 1
    assert "web_search_hits" not in grouped["by_dependency"]["axios"]


def test_group_enrichment_unattributed_web_search_goes_to_general():
    tool_results = [
        _tool_result("web_search", {"query": "general npm security best practices"}, {})
    ]
    grouped = _group_enrichment_by_dep(tool_results, ["left-pad"])

    assert grouped["by_dependency"]["left-pad"] == {}
    assert len(grouped["general"]) == 1
    assert grouped["general"][0]["tool"] == "web_search"


def test_group_enrichment_skips_errored_tool_results():
    tool_results = [
        _tool_result(
            "blast_radius",
            {"package_name": "left-pad"},
            {},
            error="container timed out",
        )
    ]
    grouped = _group_enrichment_by_dep(tool_results, ["left-pad"])

    assert grouped["by_dependency"]["left-pad"] == {}
    assert grouped["general"] == []


def test_grounded_blast_radius_returns_summary_when_available():
    tool_results = [
        _tool_result(
            "blast_radius",
            {"package_name": "left-pad"},
            {
                "package_name": "left-pad",
                "available": True,
                "affected_file_count": 1,
                "affected_files": ["scripts/build.js:1"],
                "production_file_count": 0,
                "isolated_to_tests_or_scripts": True,
                "node_count": 3,
                "depth_searched": 3,
            },
        )
    ]

    summary = _grounded_blast_radius(tool_results, "left-pad")

    assert summary is not None
    assert summary.affected_file_count == 1
    assert summary.isolated_to_tests_or_scripts is True
    # extra "package_name" key on the tool output must not leak onto the model
    assert not hasattr(summary, "package_name")


def test_grounded_blast_radius_returns_none_when_unavailable_or_missing():
    tool_results = [
        _tool_result(
            "blast_radius",
            {"package_name": "left-pad"},
            {"available": False, "error": "codegraph index not built for this repo"},
        )
    ]

    assert _grounded_blast_radius(tool_results, "left-pad") is None
    assert _grounded_blast_radius([], "axios") is None
