"""Mocked risk score node."""

import asyncio
import random

from src.graphs.risk_score.state import RiskScoreState


async def analyze(state: RiskScoreState) -> dict:
    await asyncio.sleep(random.uniform(60, 180))
    return {
        "risk_score_result": {
            "status": "mocked",
            "packages_checked": len(state.get("direct_dependencies", [])),
            "overall_score": None,
            "breakdown": {},
            "note": "Risk score subgraph not yet implemented",
        }
    }
