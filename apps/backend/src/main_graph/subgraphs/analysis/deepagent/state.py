"""Root state schema for the analysis subgraph's deep agent.

Verified against deepagents==0.6.12
(deepagents/middleware/subagents.py::_return_command_with_state_update):
every key a CompiledSubAgent's runnable returns, other than
messages/todos/structured_response, merges into the ROOT deep agent's state
through ordinary LangGraph reducers via Command(update=...). bundle_ids and
agent_calls below use the same Annotated[list, operator.add] pattern
AnalysisState already uses for the same purpose.
"""

from __future__ import annotations

import operator
from typing import Annotated

from deepagents import DeepAgentState


class AnalysisDeepAgentState(DeepAgentState):
    job_id: str
    prep_result_id: str
    bundle_ids: Annotated[list[str], operator.add]
    agent_calls: Annotated[list[dict], operator.add]
