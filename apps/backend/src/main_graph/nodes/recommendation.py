"""recommendation node — stub.

Full agentic implementation specified in 2026-05-17-stage3-synthesis-design.md.
"""

import logging

from src.main_graph.state import MainState

_log = logging.getLogger(__name__)


async def recommendation(state: MainState) -> dict:
    """Generate alternatives for high-risk deps — stub returns empty recommendations."""
    high_risk_deps = state.get("high_risk_deps") or []
    recs = [
        {
            "dep_name": dep,
            "risk_summary": "stub — full analysis pending",
            "alternatives": [],
            "migration_notes": "",
        }
        for dep in high_risk_deps
    ]
    _log.info("recommendation(stub): %d deps", len(recs))
    return {"recommendations": recs}
