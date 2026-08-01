"""Node: build_project_context — lightweight LLM summary from package.json."""

from __future__ import annotations

import json
import logging
import textwrap

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.dependency_graph import (
    build_dependency_graph,
    count_dependencies,
    read_package_json,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState, ProjectMetadata
from src.utils.config import settings
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_SYSTEM = textwrap.dedent("""\
    You are analyzing a Node.js project. Given its package.json contents and the \
user's concern, write a concise summary (3-6 sentences, ≤ 150 words) that:
    - Names the project and its stated purpose
    - Lists key dependency groups most relevant to the concern
    - Flags anything immediately notable (scripts, workspaces, unusual dependencies)
    Output only the summary text.
    """).strip()


async def build_project_context(state: DiscoveryState, config: RunnableConfig) -> dict:
    error = state.get("discovery_error")
    if error:
        return {
            "project_metadata": ProjectMetadata(
                name="unknown",
                package_manager="unknown",
                direct_dependencies_count=0,
                transitive_dependencies_count=0,
            ),
            "project_context": f"Discovery failed: {error}",
        }

    svc = get_services(config)
    repo_path = state.get("repo_path", "")
    concern = state.get("concern", "")
    pkg = read_package_json(repo_path)
    pm = state.get("detected_package_manager", "npm")

    # A freshly-generated lockfile was resolved against the live registry
    # this run, so it is NOT a pure function of commit_sha alone and must not
    # be cached indefinitely — mirrors save_prep_result's identical check.
    # This node and save_prep_result share the same trivy-scan cache key, so
    # whichever of the two runs first (this one, per discovery graph order)
    # pays for the real scan and the other is a cache hit.
    lock_committed = not state.get("lockfile_generated")
    graph = await build_dependency_graph(
        repo_path,
        pm,
        container=svc["container"],
        docker_image=settings.trivy_image,
        pkg=pkg,
        cache=svc.get("input_cache") if lock_committed else None,
        repo_url=state.get("repo_url", ""),
        commit_sha=state.get("commit_sha") or "",
    )
    direct, transitive = count_dependencies(graph)

    metadata = ProjectMetadata(
        name=pkg.get("name", "unknown"),
        package_manager=pm,
        direct_dependencies_count=direct,
        transitive_dependencies_count=transitive,
    )

    pkg_summary = json.dumps(
        {
            k: pkg.get(k)
            for k in (
                "name",
                "version",
                "description",
                "scripts",
                "dependencies",
                "devDependencies",
                "workspaces",
            )
            if pkg.get(k)
        },
        indent=2,
    )[:3000]  # cap to avoid token overflow

    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": f"Concern: {concern}\n\npackage.json:\n{pkg_summary}",
            },
        ]
    )

    logger.info(
        "build_project_context: project=%s pm=%s direct=%d",
        metadata["name"],
        pm,
        direct,
    )
    return {"project_metadata": metadata, "project_context": response.content}
