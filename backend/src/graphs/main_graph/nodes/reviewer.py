"""Reviewer node — quality-checks the summary (mocked)."""

import asyncio
import random

from src.graphs.main_graph.state import MainState


async def reviewer(state: MainState) -> dict:
    await asyncio.sleep(random.uniform(2, 8))
    return {"review": "[Mocked] Review complete. Reviewer not yet implemented."}
