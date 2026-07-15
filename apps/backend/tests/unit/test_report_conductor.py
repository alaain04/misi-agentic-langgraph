from __future__ import annotations


def test_build_system_lists_all_registered_tools():
    from src.main_graph.subgraphs.report.nodes.report_conductor import _build_system
    from src.main_graph.subgraphs.report.utils.registry import REPORT_TOOL_DESCRIPTIONS

    system = _build_system(6)

    for name in REPORT_TOOL_DESCRIPTIONS:
        assert name in system


def test_build_system_includes_max_iter():
    from src.main_graph.subgraphs.report.nodes.report_conductor import _build_system
    system = _build_system(6)
    assert "6" in system
