from __future__ import annotations

from src.db.connection import get_db
from src.models.results import AnalysisResult, EvidenceBundle, PrepResult, ReportResult


class ResultDAO:
    def __init__(self) -> None:
        db = get_db()
        self._prep = db["prep_results"]
        self._bundles = db["evidence_bundles"]
        self._analysis = db["analysis_results"]
        self._report = db["report_results"]

    async def save_prep(self, result: PrepResult) -> str:
        await self._prep.insert_one(result.model_dump())
        return result.id

    async def get_prep(self, result_id: str) -> PrepResult:
        doc = await self._prep.find_one({"id": result_id}, {"_id": 0})
        if doc is None:
            raise LookupError(f"PrepResult not found: {result_id}")
        return PrepResult(**doc)

    async def save_bundle(self, bundle: EvidenceBundle) -> str:
        await self._bundles.insert_one(bundle.model_dump())
        return bundle.id

    async def get_bundles(self, ids: list[str]) -> list[EvidenceBundle]:
        cursor = self._bundles.find({"id": {"$in": ids}}, {"_id": 0})
        return [EvidenceBundle(**doc) async for doc in cursor]

    async def save_analysis(self, result: AnalysisResult) -> str:
        await self._analysis.insert_one(result.model_dump())
        return result.id

    async def get_analysis(self, result_id: str) -> AnalysisResult:
        doc = await self._analysis.find_one({"id": result_id}, {"_id": 0})
        if doc is None:
            raise LookupError(f"AnalysisResult not found: {result_id}")
        return AnalysisResult(**doc)

    async def save_report(self, result: ReportResult) -> str:
        await self._report.insert_one(result.model_dump())
        return result.id

    async def get_report(self, result_id: str) -> ReportResult:
        doc = await self._report.find_one({"id": result_id}, {"_id": 0})
        if doc is None:
            raise LookupError(f"ReportResult not found: {result_id}")
        return ReportResult(**doc)
