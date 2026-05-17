"""Impact analysis node — stub.

Full agentic implementation specified in 2026-05-17-stage2-impact-design.md.
"""

import logging

from src.main_graph.subgraphs.ingestion_subgraphs.impact.dao import impact_dao
from src.main_graph.subgraphs.ingestion_subgraphs.impact.models import ImpactEntry
from src.main_graph.subgraphs.ingestion_subgraphs.impact.state import ImpactState

_log = logging.getLogger(__name__)


async def analyze(state: ImpactState) -> dict:
    dep_name = state.get("dependency_name", "")
    _log.info("impact.analyze(stub): dep=%s", dep_name)
    entry = ImpactEntry(dep_name=dep_name)
    result_id = await impact_dao.save(entry)
    return {"result_id": result_id}
