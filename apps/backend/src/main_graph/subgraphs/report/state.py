from __future__ import annotations

import operator
from typing import Annotated, NotRequired

from typing_extensions import TypedDict


class ReportState(TypedDict):
    # From MainState
    job_id: str
    concern: str
    prep_result_id: str
    analysis_result_id: str

    # Internal
    findings_to_enrich: NotRequired[list[dict]]  # FindingNote.model_dump() list
    all_flagged_dep_names: NotRequired[list[str]]
    current_finding: NotRequired[dict]  # FindingNote.model_dump() for finding_enricher
    enriched_findings: Annotated[list[dict], operator.add]  # ReportFinding.model_dump()

    # Output
    report_result_id: NotRequired[str]
