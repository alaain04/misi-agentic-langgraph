from typing import Any, NotRequired

from typing_extensions import TypedDict


class CrossAnalyzerState(TypedDict):
    subgraph_results: list[dict]
    discovery_summary: str
    concern: str
    reviewer_feedback: NotRequired[str]
    review_iterations: NotRequired[int]
    analysis_report: NotRequired[dict[str, Any]]
