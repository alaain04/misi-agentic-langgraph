from bson import ObjectId

from src.db.connection import get_db
from src.main_graph.subgraphs.discovery.models import SbomEntry


class SbomDAO:
    @property
    def _col(self):
        return get_db()["sbom_gens"]

    async def save(self, entry: SbomEntry) -> str:
        result = await self._col.insert_one(entry.model_dump())
        return str(result.inserted_id)

    async def get(self, doc_id: str) -> dict | None:
        doc = await self._col.find_one({"_id": ObjectId(doc_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc


sbom_dao = SbomDAO()
