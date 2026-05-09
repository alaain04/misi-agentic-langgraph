"""Supply chain analysis node."""

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
    sbom = state.get("sbom_cyclonedx", {})
    concern = state.get("concern", "")
    components = sbom.get("components", [])

    records = [
        SupplyChainRecord(
            name=comp["name"],
            version=comp.get("version", "unknown"),
            risk_score=0.1,
            flags=["mock-data"],
        )
        for comp in components[:10]
    ]

    entry = SupplyChainEntry(
        records=records,
        high_risk_count=sum(1 for r in records if r.risk_score >= 0.7),
        concern=concern,
    )
    result_id = await supply_chain_dao.save(entry)
    logger.info("supply_chain: saved %d records, result_id=%s", len(records), result_id)
    return {"result_id": result_id}
