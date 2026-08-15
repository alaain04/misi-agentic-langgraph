from __future__ import annotations

from src.db.input_cache import InputCacheDAO
from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.analysis.agents.base_agent import BaseAgent
from src.main_graph.tools.external_api import package_health_data
from src.models.results import AgentDispatch, EvidenceBundle, PrepResult
from src.utils.dependency_graph import is_direct


class MaintenanceAgent(BaseAgent):
    agent_type = "maintenance_agent"
    concern_types = frozenset({"maintenance"})
    description = (
        "Assesses package health and abandonment risk by checking release "
        "recency and weekly download volume. Use when concern involves outdated, "
        "unmaintained, or deprecated packages."
    )
    system_prompt = """
        You are a package maintenance and health specialist for Node.js dependencies.
        Your task: {hypothesis}
        Packages to focus on: {packages}

        Available tools:
        {tool_descriptions}

        Investigation strategy:
        1. If specific packages are named above ("Packages to focus on"), call \
package_health_data(packages=<that list>) first so you have direct data for \
exactly those packages. Otherwise (or in addition), call package_health_data() \
with no arguments for a broad scan of the repo's first several direct \
dependencies.
        2. Check `checked` against `total_deps` in each result. If `checked` < \
`total_deps`, more direct dependencies exist than were scanned -- if any \
package named in "Packages to focus on" is missing from the result's \
`packages`/`errors`, call package_health_data(packages=[<missing names>]) \
to fetch them directly; you are not limited to the default scan's cap.
        3. For each package, weigh release recency against weekly_downloads \
before deciding it is a risk:
           - Strong current adoption overrides staleness alone. A package \
with weekly_downloads at or above roughly 1,000 is meaningfully in active \
use — many mature, stable libraries go a long time between releases \
without that meaning anything is wrong. Never flag such a package as a \
maintenance risk based on days_since_last_release alone.
           - A package IS a maintenance risk if: days_since_last_release is \
more than 365 AND weekly_downloads is low (below roughly 1,000) or \
missing/errored — OR days_since_created is less than 90 AND \
weekly_downloads is low or missing/errored.
        4. Record the package name, days_since_last_release, weekly_downloads, \
and risk rationale in each FindingNote so the downloads-vs-staleness \
tradeoff is visible to a reviewer.

        Scope:
        - Only assess DIRECT dependencies (declared in package.json). Do not
          create maintenance findings for transitive dependencies — their health
          is the direct parent's responsibility and is not directly actionable.

        Rules:
        - Never repeat a tool call with the same arguments.
        - If your data for any focused package is incomplete (missing from a \
result, or listed in `errors`) after step 2, note this in your summary and \
cap confidence at 0.8 rather than treating it as resolved.
        - Set finalize=true when you have assessed all flagged packages.
        - After {max_iter} iterations, set finalize=true regardless.
        - confidence > 0.8: you have data for all focused packages.
        - confidence 0.5-0.8: partial data, some packages returned no results.
        - confidence < 0.5: tools returned errors or no data.
        """

    def _agent_tools(self) -> list:
        return [package_health_data]

    async def run(
        self,
        dispatch: AgentDispatch,
        prep: PrepResult,
        container: ContainerRunPort | None = None,
        cache: InputCacheDAO | None = None,
    ) -> tuple[EvidenceBundle, list[str], int]:
        """Maintenance is a quality-proxy analysis: "old"/"unmaintained" is only
        actionable for a dependency the user actually chose. A stale transitive
        under a healthy direct parent is the parent maintainer's concern, not an
        actionable risk here, so transitive findings are dropped deterministically
        (prompt guidance alone has leaked such findings before). When the graph
        has no transitive data (package.json fallback), directness is unknowable,
        so findings are kept rather than silently discarded.
        """
        bundle, tools_used, iterations = await super().run(dispatch, prep, container)
        has_direct = prep.dependency_graph.get("direct")
        has_packages = prep.dependency_graph.get("packages")
        if not has_direct or not has_packages:
            return bundle, tools_used, iterations
        direct_only = [
            f for f in bundle.findings if is_direct(prep.dependency_graph, f.dep_name)
        ]
        if len(direct_only) != len(bundle.findings):
            bundle = bundle.model_copy(update={"findings": direct_only})
        return bundle, tools_used, iterations
