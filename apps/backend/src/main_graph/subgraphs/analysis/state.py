from __future__ import annotations

import operator
from typing import Annotated, NotRequired

from typing_extensions import TypedDict

from src.models.results import AnalysisConductorDecision


class AnalysisState(TypedDict):
    # From MainState (matched by key name)
    job_id: str
    concern: str
    prep_result_id: str

    # Internal
    conductor_decision: NotRequired[AnalysisConductorDecision]
    current_dispatch: NotRequired[dict]   # AgentDispatch.model_dump() for domain_agent nodes
    bundle_ids: Annotated[list[str], operator.add]
    conductor_iteration: NotRequired[int]

    # Output (written back to MainState)
    analysis_result_id: NotRequired[str]
