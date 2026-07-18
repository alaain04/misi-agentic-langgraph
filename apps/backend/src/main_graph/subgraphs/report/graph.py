from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.report.nodes.report_conductor import report_conductor
from src.main_graph.subgraphs.report.nodes.report_tool_runner import report_tool_runner
from src.main_graph.subgraphs.report.nodes.save_report_result import save_report_result
from src.main_graph.subgraphs.report.state import ReportState


def _after_conductor(state: ReportState) -> str:
    decision = state.get("conductor_decision")
    if not decision or decision.finalize:
        return "save_report_result"
    if decision.tool_calls:
        return "report_tool_runner"
    return "save_report_result"


def build_report_subgraph():
    builder = StateGraph(ReportState)

    builder.add_node("report_conductor", report_conductor)
    builder.add_node("report_tool_runner", report_tool_runner)
    builder.add_node("save_report_result", save_report_result)

    builder.add_edge(START, "report_conductor")
    builder.add_conditional_edges(
        "report_conductor",
        _after_conductor,
        ["report_tool_runner", "save_report_result"],
    )
    builder.add_edge("report_tool_runner", "report_conductor")
    builder.add_edge("save_report_result", END)

    return builder.compile()


report_subgraph = build_report_subgraph()
