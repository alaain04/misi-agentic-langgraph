from __future__ import annotations

from typing import Annotated, NotRequired

from typing_extensions import TypedDict

from src.main_graph.subgraphs.remediation.deepagent.state import _merge_replace


class RemediationState(TypedDict):
    job_id: str
    concern: str
    prep_result_id: str
    analysis_result_id: str
    remediation_result_id: NotRequired[str]
    targets: NotRequired[dict[str, dict]]
    remediations: NotRequired[Annotated[dict[str, dict], _merge_replace]]
    requires_edges: NotRequired[Annotated[dict[str, list], _merge_replace]]
    retry_targets: NotRequired[list[str]]
    correction_rounds: NotRequired[int]
