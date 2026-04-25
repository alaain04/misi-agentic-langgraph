"""Recommender node — generates actionable recommendations (mocked)."""

import asyncio
import random

from src.graphs.main_graph.state import MainState


async def recommender(state: MainState) -> dict:
    await asyncio.sleep(random.uniform(2, 8))
    return {
        "recommendation": "[Mocked] \
            Recommendations generated. Recommender not yet implemented."
    }
