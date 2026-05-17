# backend/src/main_graph/subgraphs/ingestion_subgraphs/_base.py
from typing import Any, NotRequired

from typing_extensions import TypedDict


class AnalysisState(TypedDict):
    sbom_cyclonedx: dict[str, Any]
    discovery_summary: str
    concern: str
    upstream_results: NotRequired[dict[str, Any]]
    repo_path: NotRequired[str]
    dependency_name: NotRequired[str]
