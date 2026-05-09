from bson import ObjectId

from src.db.connection import get_db
from src.main_graph.subgraphs.ingestion_subgraphs.sbom_gen.models import (
    SbomGenEntry,
)


class SbomGenDAO:
    @property
    def _col(self):
        return get_db()["sbom_gens"]

    async def save(self, entry: SbomGenEntry) -> str:
        result = await self._col.insert_one(entry.model_dump())
        return str(result.inserted_id)

    async def get(self, doc_id: str) -> dict | None:
        doc = await self._col.find_one({"_id": ObjectId(doc_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc


sbom_gen_dao = SbomGenDAO()
