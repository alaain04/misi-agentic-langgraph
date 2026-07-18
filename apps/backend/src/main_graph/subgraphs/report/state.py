from __future__ import annotations

import operator
from typing import Annotated, NotRequired

from typing_extensions import TypedDict

from src.models.conductor import ToolResult
from src.models.results import ReportConductorDecision


class ReportState(TypedDict):
    # From MainState
    job_id: str
    concern: str
    prep_result_id: str
    analysis_result_id: str

    # Internal
    conductor_decision: NotRequired[ReportConductorDecision]
    tool_results: Annotated[list[ToolResult], operator.add]
    conductor_iteration: NotRequired[int]

    # Output
    report_result_id: NotRequired[str]
