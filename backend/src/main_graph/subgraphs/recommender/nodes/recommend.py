import asyncio
import random

from src.main_graph.subgraphs.recommender.state import RecommenderState


async def recommend(state: RecommenderState) -> dict:
    await asyncio.sleep(random.uniform(2, 8))
    return {
        "recommendation": (
            "[Mocked] Recommendations generated. Recommender not yet implemented."
        )
    }
