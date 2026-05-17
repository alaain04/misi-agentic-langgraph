"""Report reviewer node — LLM evaluates the cross-analyzer report.

Returns review_approved=True if the report is acceptable, or
review_approved=False with reviewer_feedback requesting a rebuild.
"""

import json
import logging

from src.main_graph.subgraphs.report_reviewer.state import ReportReviewerState
from src.utils.llm import Model, get_llm, parse_llm_json

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_SYSTEM_PROMPT = (
    "You are a quality reviewer for dependency risk analysis reports."
    " You receive a JSON report and a user concern."
    " Check that the report covers the concern, contains at least one domain result,"
    " and has a coherent structure."
    ' If acceptable, return exactly: {"approved": true}'
    " If it needs improvement, return:"
    ' {"approved": false, "feedback": "<specific issues>"}'
    " Return only valid JSON, no extra text."
)


async def review(state: ReportReviewerState) -> dict:
    report = state.get("analysis_report", {})
    concern = state.get("concern", "")

    report_snippet = json.dumps(report, default=str)[:3000]

    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Concern: {concern}\n\nReport:\n{report_snippet}",
            },
        ]
    )

    try:
        parsed = parse_llm_json(response.content or "")
        approved = bool(parsed.get("approved", True))
        feedback = parsed.get("feedback") if not approved else None
    except Exception:
        logger.warning(
            "report_reviewer: failed to parse LLM response, approving by default"
        )
        approved = True
        feedback = None

    logger.info("report_reviewer: approved=%s feedback=%s", approved, feedback)
    return {"review_approved": approved, "reviewer_feedback": feedback or None}
