"""Cross-analyzer node — mocked implementation.

Bundles subgraph_results into a structured JSON report and looks for
correlations between domains. Incorporates reviewer_feedback when rebuilding.
"""

import logging
from datetime import UTC, datetime

from src.main_graph.subgraphs.cross_analyzer.state import CrossAnalyzerState

logger = logging.getLogger(__name__)


def _group_by_domain(subgraph_results: list[dict]) -> dict:
    domains: dict[str, list] = {
        "vulnerabilities": [],
        "license_compliance": [],
        "supply_chain": [],
    }
    for item in subgraph_results:
        name = item.get("subgraph", "")
        if name in domains:
            domains[name].append(item)
    return domains


async def analyze(state: CrossAnalyzerState) -> dict:
    results = state.get("subgraph_results", [])
    concern = state.get("concern", "")
    feedback = state.get("reviewer_feedback")
    iteration = state.get("review_iterations", 0)

    if feedback:
        logger.info(
            "cross_analyzer: rebuilding report on iteration %d — feedback: %s",
            iteration,
            feedback,
        )

    domains = _group_by_domain(results)

    report: dict = {
        "concern": concern,
        "generated_at": datetime.now(UTC).isoformat(),
        "iteration": iteration + 1,
        "total_subgraph_results": len(results),
        "domains": {
            name: {"result_count": len(items), "results": items}
            for name, items in domains.items()
        },
        "correlations": [],
        "reviewer_notes": feedback or None,
    }

    logger.info("cross_analyzer: report built, domains=%s", list(domains.keys()))
    return {
        "analysis_report": report,
        "review_iterations": iteration + 1,
        "reviewer_feedback": None,
    }
