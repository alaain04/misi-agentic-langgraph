from __future__ import annotations

import textwrap
from typing import cast

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.registry import (
    agents_for_types,
    get_agent_descriptions,
)
from src.main_graph.subgraphs.analysis.concern import (
    Concern,
    ConcernDraft,
    invalid_concern,
    packages_valid,
)
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.utils.llm import Model, get_llm

_llm = get_llm(Model.GPT_5_4_MINI)

_UNDERSTAND_CONCERN_SYSTEM = textwrap.dedent("""\
    Classify a user's dependency-risk concern for a Node.js project into a
    structured form.

    Available specialist agents (context for what each concern type means --
    you are not selecting agents here, only classifying the concern):
    {agent_roster}

    Direct dependencies (name@installed_version): {direct_deps}

    Rules:
    - is_valid: false if the input is not a dependency-risk concern at all --
      a greeting, small talk, an unrelated question, or anything that isn't
      asking to analyze this project's dependencies. True otherwise. When
      false, still fill in the other fields with these exact placeholders
      (they are ignored): type=["other"], packages=[],
      requires_per_dependency_analysis=false.
    - type: one or more of "vulnerability", "license", "maintenance",
      "supply_chain", "web_research", "other" -- every concept explicitly
      present in the concern. Do not add types the concern doesn't mention.
    - packages: the specific package name(s) the concern names, extracted
      exactly as they appear in the direct dependencies list above (no
      version suffix). Empty if the concern doesn't name particular
      package(s).
    - requires_per_dependency_analysis: true only if the concern explicitly
      asks for a per-package/per-dependency breakdown or similarly
      exhaustive individual treatment of every dependency. False for a
      general/aggregate risk read.
    """).strip()


def _roster() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in get_agent_descriptions().items())


async def understand_concern(state: AnalysisState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    prep = await svc["result_dao"].get_prep(state["prep_result_id"])
    direct_deps = prep.dependency_graph.get("direct", {})

    structured = _llm.with_structured_output(ConcernDraft, method="function_calling")
    draft = cast(
        ConcernDraft,
        await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": _UNDERSTAND_CONCERN_SYSTEM.format(
                        agent_roster=_roster(),
                        direct_deps=[f"{n}@{v}" for n, v in direct_deps.items()],
                    ),
                },
                {"role": "user", "content": state["concern"]},
            ]
        ),
    )

    if draft.is_valid and packages_valid(draft.packages, direct_deps):
        concern = Concern(
            is_valid=True,
            type=draft.type,
            scope="specific_packages" if draft.packages else "all_dependencies",
            packages=draft.packages,
            requires_per_dependency_analysis=draft.requires_per_dependency_analysis,
            preferred_agents=agents_for_types(draft.type),
        )
    else:
        concern = invalid_concern()

    return {"structured_concern": concern.model_dump()}
