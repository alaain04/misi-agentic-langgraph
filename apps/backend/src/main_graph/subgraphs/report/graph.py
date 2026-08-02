from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from src.main_graph.subgraphs.report.nodes.enrichment_collector import (
    enrichment_collector,
)
from src.main_graph.subgraphs.report.nodes.finding_enricher import finding_enricher
from src.main_graph.subgraphs.report.nodes.report_intake import report_intake
from src.main_graph.subgraphs.report.nodes.report_synthesizer import (
    report_synthesizer,
)
from src.main_graph.subgraphs.report.state import ReportState


def _dispatch_findings(state: ReportState):
    """
    Route from report_intake.

    Returns a list[Send] to fan out to parallel finding_enricher invocations,
    or the string "save_report_result" to finalize with no findings.

    Note: Send-based fan-out must happen from a conditional EDGE function,
    not from a node — LangGraph 1.x does not support list[Send] node returns.
    """
    findings = state.get("findings_to_enrich") or []
    if not findings:
        return "save_report_result"
    return [Send("finding_enricher", {**state, "current_finding": f}) for f in findings]


def build_report_subgraph():
    builder = StateGraph(ReportState)

    builder.add_node("report_intake", report_intake)
    builder.add_node("finding_enricher", finding_enricher)
    builder.add_node("enrichment_collector", enrichment_collector)
    # Node kept as "save_report_result" (not "report_synthesizer") so
    # _dispatch_findings's return value and existing routing tests don't
    # need to change — only the function/file identity was renamed.
    builder.add_node("save_report_result", report_synthesizer)

    builder.add_edge(START, "report_intake")
    builder.add_conditional_edges("report_intake", _dispatch_findings)
    builder.add_edge("finding_enricher", "enrichment_collector")
    builder.add_edge("enrichment_collector", "save_report_result")
    builder.add_edge("save_report_result", END)

    return builder.compile()


report_subgraph = build_report_subgraph()
