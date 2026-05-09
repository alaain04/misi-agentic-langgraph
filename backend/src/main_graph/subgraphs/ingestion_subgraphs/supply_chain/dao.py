from bson import ObjectId

from src.db.connection import get_db
from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.models import (
    SupplyChainEntry,
)


class SupplyChainDAO:
    @property
    def _col(self):
        return get_db()["supply_chain"]

    async def save(self, entry: SupplyChainEntry) -> str:
        result = await self._col.insert_one(entry.model_dump())
        return str(result.inserted_id)

    async def get(self, doc_id: str) -> dict | None:
        doc = await self._col.find_one({"_id": ObjectId(doc_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc


supply_chain_dao = SupplyChainDAO()
