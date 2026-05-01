import asyncio
import random

from src.main_graph.subgraphs.reviewer.state import ReviewerState


async def review(state: ReviewerState) -> dict:
    await asyncio.sleep(random.uniform(2, 8))
    return {"review": "[Mocked] Review complete. Reviewer not yet implemented."}
