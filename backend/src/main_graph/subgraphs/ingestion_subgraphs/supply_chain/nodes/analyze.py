"""Supply chain analysis node — mocked implementation."""

import logging

from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.dao import (
    supply_chain_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.models import (
    SupplyChainEntry,
    SupplyChainRecord,
)
from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.state import (
    SupplyChainState,
)

logger = logging.getLogger(__name__)


async def analyze(state: SupplyChainState) -> dict:
    deps = state.get("direct_dependencies", [])
    concern = state.get("concern", "")

    records = [
        SupplyChainRecord(
            name=dep["name"],
            version=dep.get("version_spec", "unknown"),
            risk_score=0.1,
            flags=["mock-data"],
        )
        for dep in deps[:10]
    ]

    entry = SupplyChainEntry(
        records=records,
        high_risk_count=sum(1 for r in records if r.risk_score >= 0.7),
        concern=concern,
    )
    result_id = await supply_chain_dao.save(entry)
    logger.info("supply_chain: saved %d records, result_id=%s", len(records), result_id)
    return {"result_id": result_id}
