from __future__ import annotations

import textwrap
from typing import cast

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.registry import get_agent_descriptions
from src.main_graph.subgraphs.analysis.concern import Concern
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.utils.llm import Model, get_llm

_llm = get_llm(Model.GPT_5_4_MINI)

_UNDERSTAND_CONCERN_SYSTEM = textwrap.dedent("""\
    Classify a user's dependency-risk concern for a Node.js project into a
    structured form.

    Available specialist agents (valid values for preferred_agents):
    {agent_roster}

    Direct dependencies (name@installed_version): {direct_deps}

    Rules:
    - is_valid: false if the input is not a dependency-risk concern at all --
      a greeting, small talk, an unrelated question, or anything that isn't
      asking to analyze this project's dependencies. True otherwise. When
      false, still fill in the other fields with these exact placeholders
      (they are ignored): type=["other"], scope="all_dependencies",
      packages=[], requires_per_dependency_analysis=false,
      preferred_agents=[].
    - type: one or more of "vulnerability", "license", "maintenance",
      "supply_chain", "web_research", "other" -- every concept explicitly
      present in the concern. Do not add types the concern doesn't mention.
    - scope: "specific_packages" if the concern names particular package(s);
      otherwise "all_dependencies".
    - packages: the specific package names if scope is "specific_packages",
      else empty.
    - requires_per_dependency_analysis: true only if the concern explicitly
      asks for a per-package/per-dependency breakdown or similarly
      exhaustive individual treatment of every dependency. False for a
      general/aggregate risk read.
    - preferred_agents: the specialist agent_type(s) from the roster above
      best suited to investigate this concern -- vulnerability_agent for
      "vulnerability", license_agent for "license", maintenance_agent for
      "maintenance", supply_chain_agent for "supply_chain",
      web_research_agent for "web_research" or "other".
    """).strip()


def _roster() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in get_agent_descriptions().items())


async def understand_concern(state: AnalysisState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    prep = await svc["result_dao"].get_prep(state["prep_result_id"])
    direct_deps = [
        f"{n}@{v}" for n, v in prep.dependency_graph.get("direct", {}).items()
    ]

    structured = _llm.with_structured_output(Concern, method="function_calling")
    concern = cast(
        Concern,
        await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": _UNDERSTAND_CONCERN_SYSTEM.format(
                        agent_roster=_roster(), direct_deps=direct_deps
                    ),
                },
                {"role": "user", "content": state["concern"]},
            ]
        ),
    )
    return {"structured_concern": concern.model_dump()}
