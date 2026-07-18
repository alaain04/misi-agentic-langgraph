from __future__ import annotations

from src.main_graph.subgraphs.report.state import ReportState


async def enrichment_collector(state: ReportState) -> dict:
    """No-op fan-in node — triggers re-entry after all finding_enricher
    branches finish."""
    return {}
