from __future__ import annotations

import operator
from typing import Annotated, NotRequired

from typing_extensions import TypedDict


class AnalysisState(TypedDict):
    # From MainState (matched by key name)
    job_id: str
    concern: str
    prep_result_id: str

    # Internal — deep agent run + coverage loop
    # deepagent_state: last full state returned by deep_agent.ainvoke()
    deepagent_state: NotRequired[dict]
    structured_concern: NotRequired[dict]  # Concern.model_dump()
    missing_deps: NotRequired[list[str]]
    correction_rounds: NotRequired[int]
    whole_tree_checked_roster: NotRequired[list[str]]
    whole_tree_satisfies_concern: NotRequired[bool]
    bundle_ids: Annotated[list[str], operator.add]
    agent_calls: Annotated[
        list[dict], operator.add
    ]  # AgentCallRecord.model_dump() per domain_agent call

    # Output (written back to MainState)
    analysis_result_id: NotRequired[str]
