"""Fan-in node — all skill_executor outputs have been reduced into state.evidence by this point."""
import logging

from src.main_graph.state import MainState

logger = logging.getLogger(__name__)


def evidence_collector(state: MainState) -> dict:
    evidence = state.get("evidence") or []
    logger.info("evidence_collector: %d evidence items collected", len(evidence))
    return {}
