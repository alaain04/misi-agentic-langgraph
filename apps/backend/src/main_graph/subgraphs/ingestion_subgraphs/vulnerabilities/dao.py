from bson import ObjectId

from src.db.connection import get_db
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.models import (
    VulnerabilitiesEntry,
)


class VulnerabilitiesDAO(IngestionResultPort):
    @property
    def _col(self):
        return get_db()["vulnerabilities"]

    async def save(self, entry: VulnerabilitiesEntry) -> str:
        result = await self._col.insert_one(entry.model_dump())
        return str(result.inserted_id)

    async def get(self, doc_id: str) -> dict | None:
        doc = await self._col.find_one({"_id": ObjectId(doc_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc


vulnerabilities_dao = VulnerabilitiesDAO()
