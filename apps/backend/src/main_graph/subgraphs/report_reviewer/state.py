from typing import Any, NotRequired

from typing_extensions import TypedDict


class ReportReviewerState(TypedDict):
    analysis_report: dict[str, Any]
    concern: str
    review_approved: NotRequired[bool]
    reviewer_feedback: NotRequired[str]
