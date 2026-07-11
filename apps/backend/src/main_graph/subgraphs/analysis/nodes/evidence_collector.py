from __future__ import annotations

from src.main_graph.subgraphs.analysis.state import AnalysisState


async def evidence_collector(state: AnalysisState) -> dict:
    """No-op fan-in node — triggers conductor re-entry after all domain agents finish."""
    return {}
