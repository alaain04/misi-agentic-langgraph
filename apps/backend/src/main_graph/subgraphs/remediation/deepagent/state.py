from __future__ import annotations

from typing import Annotated

from deepagents import DeepAgentState


def _keep_first_str(current: str, incoming: str) -> str:
    return current or incoming


def _keep_first_dict(current: dict, incoming: dict) -> dict:
    return current or incoming


def _merge_replace(current: dict, incoming: dict) -> dict:
    """Dict-keyed merge where the incoming write wins per key. Used for
    per-target accumulation so a retry round's fresh outcome for a target
    replaces its earlier attempt instead of appending a duplicate, and so
    two parallel task() calls writing different target keys in the same
    superstep merge cleanly with no ordering requirement between them."""
    return {**current, **incoming}


class RemediationDeepAgentState(DeepAgentState):
    job_id: Annotated[str, _keep_first_str]
    prep_result_id: Annotated[str, _keep_first_str]
    targets: Annotated[dict[str, dict], _keep_first_dict]
    remediations: Annotated[dict[str, dict], _merge_replace]
    requires_edges: Annotated[dict[str, list], _merge_replace]
    migration_plans: Annotated[dict[str, dict], _merge_replace]
