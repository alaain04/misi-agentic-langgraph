"""Root state schema for the analysis subgraph's deep agent.

Verified against deepagents==0.6.12
(deepagents/middleware/subagents.py::_return_command_with_state_update):
every key a CompiledSubAgent's runnable returns, other than
messages/todos/structured_response, merges into the ROOT deep agent's state
through ordinary LangGraph reducers via Command(update=...). bundle_ids and
agent_calls below use the same Annotated[list, operator.add] pattern
AnalysisState already uses for the same purpose.

job_id and prep_result_id need their own reducer for a different reason: the
root deep agent's LLM can (and, per the system prompt in nodes.py, is
actively encouraged to) emit MULTIPLE task() calls in a single turn. Every
CompiledSubAgent's own state schema (_SubagentState in subagent_wrapper.py)
carries job_id/prep_result_id through unchanged from the root state and
returns them in its Command(update=...), so N parallel task() calls in one
superstep produce N identical writes to these keys. Plain str fields compile
to LangGraph LastValue channels, which accept at most one write per
superstep and raise InvalidUpdateError on the second -- crashing the whole
job. Since job_id/prep_result_id are invariant for the life of a run, a
reducer that just keeps the existing value (falling back to the incoming one
only if unset) tolerates any number of concurrent identical writes safely.
"""

from __future__ import annotations

import operator
from typing import Annotated

from deepagents import DeepAgentState


def _keep_first(current: str, incoming: str) -> str:
    """Reducer for LastValue-incompatible parallel writes of an
    invariant-per-run value: prefer whatever is already set."""
    return current or incoming


class AnalysisDeepAgentState(DeepAgentState):
    job_id: Annotated[str, _keep_first]
    prep_result_id: Annotated[str, _keep_first]
    bundle_ids: Annotated[list[str], operator.add]
    agent_calls: Annotated[list[dict], operator.add]
