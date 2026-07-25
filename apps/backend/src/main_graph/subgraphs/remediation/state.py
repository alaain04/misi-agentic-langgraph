from __future__ import annotations

from typing import NotRequired

from typing_extensions import TypedDict


class RemediationState(TypedDict):
    job_id: str
    concern: str
    prep_result_id: str
    analysis_result_id: str
    remediation_result_id: NotRequired[str]
