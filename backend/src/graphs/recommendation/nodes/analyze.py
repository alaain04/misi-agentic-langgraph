"""Mocked recommendation node."""

import asyncio
import random

from src.graphs.recommendation.state import RecommendationState


async def analyze(state: RecommendationState) -> dict:
    await asyncio.sleep(random.uniform(60, 180))
    return {
        "recommendation_result": {
            "status": "mocked",
            "packages_checked": len(state.get("direct_dependencies", [])),
            "recommendations": [],
            "note": "Recommendation subgraph not yet implemented",
        }
    }
