from __future__ import annotations

import asyncio
import inspect
import logging
import os
import textwrap
import time
import uuid
from typing import cast

from langchain_core.tools import tool

from src.main_graph.subgraphs.report.agents.critique import _format_tool_results
from src.main_graph.tools.blast_radius import make_blast_radius_tool
from src.main_graph.tools.package_files import read_file as _read_file_impl
from src.main_graph.tools.search_code import _store_cache, is_indexable_source_file
from src.models.conductor import FindingNote, ToolCall, ToolResult
from src.models.results import BlastRadiusSummary, ImpactAnalysisDecision, PrepResult
from src.utils.config import settings
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 3
_SNIPPET_RADIUS = 150
_llm = get_llm(Model.GPT_5_4_MINI)
_BLAST_RADIUS_FIELDS = {
    "affected_file_count",
    "affected_files",
    "production_file_count",
    "isolated_to_tests_or_scripts",
    "node_count",
    "depth_searched",
}

_TOOL_DESCRIPTIONS = {
    "blast_radius": "blast_radius(depth: int = 3): real import/usage graph blast "
    "radius for this package via codegraph — affected file count/paths, whether "
    "usage is isolated to tests/scripts. Try this first.",
    "find_usage_sites": "find_usage_sites(): fuzzy semantic-search fallback; source "
    "files that actually import/use this package, with a code snippet around the "
    "match. Use only if blast_radius returned available=false.",
    "read_file": "read_file(relative_path: str): read a specific affected file's "
    "content to judge what business capability it implements. Use this on at "
    "least one affected production file before finalizing.",
}

_SYSTEM = textwrap.dedent("""\
    You are investigating the real usage impact of ONE dependency risk
    finding's package.

    Package: {dep_name}

    Available tools:
    {tool_descriptions}

    Process: call blast_radius first. If it returns available=false, call
    find_usage_sites instead. Then read_file at least one affected
    production file (skip only if none were found by either tool) before
    finalizing, so your narrative reflects what the code actually does, not
    just its path.

    When you have enough evidence, set finalize=true and write:
    - use_cases_impacted: short list of the business use cases/capabilities
      the affected code implements (derived only from what you actually
      read via read_file, never invented). Empty list if nothing was
      affected or nothing was readable.
    - narrative: 1-3 sentences summarizing the real-world impact. If no
      tool returned usable data, say the impact could not be determined —
      never guess.

    Do not report file counts, paths, or availability yourself — those are
    captured automatically from tool output, not from your response.

    After {max_iter} iterations, set finalize=true regardless of coverage.
    """).strip()


def _snippet_around_match(content: str, needle: str) -> str:
    idx = content.find(needle)
    if idx == -1:
        return content[:300]
    start = max(0, idx - _SNIPPET_RADIUS)
    end = min(len(content), idx + len(needle) + _SNIPPET_RADIUS)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


def _make_find_usage_sites_tool(vector_store_id: str):
    @tool
    async def find_usage_sites(package_name: str) -> list[dict]:
        """Find source files that import or use a specific npm package, with
        enough surrounding code to tell what business logic depends on it."""
        store = _store_cache.get(vector_store_id)
        if store is None:
            return [{"error": f"Vector store {vector_store_id} not loaded"}]
        query = f"import {package_name} require {package_name}"
        results = await store.asimilarity_search(query, k=20)
        return [
            {
                "file": doc.metadata.get("file", ""),
                "snippet": _snippet_around_match(doc.page_content, package_name),
            }
            for doc in results
            if package_name in doc.page_content
            and is_indexable_source_file(os.path.basename(doc.metadata.get("file", "")))
        ]

    return find_usage_sites


def _make_read_file_tool(repo_path: str):
    @tool
    async def read_file(relative_path: str) -> dict:
        """Read a specific file from the cloned repo."""
        return await _read_file_impl(repo_path, relative_path)

    return read_file


def _build_internal_tool_map(finding: FindingNote, prep: PrepResult, container) -> dict:
    tools: dict = {
        "blast_radius": make_blast_radius_tool(
            prep.repo_path, container, settings.codegraph_docker_image
        ),
        "read_file": _make_read_file_tool(prep.repo_path),
    }
    if prep.vector_store_id:
        tools["find_usage_sites"] = _make_find_usage_sites_tool(prep.vector_store_id)
    return tools


def _format_internal_tools(tool_map: dict) -> str:
    return "\n".join(f"- {_TOOL_DESCRIPTIONS[name]}" for name in tool_map)


def _tool_callable(fn):
    """LangChain's @tool decorator stores a sync function's body in .func
    and an async function's body in .coroutine (leaving .func None) — pick
    whichever is actually callable for signature introspection."""
    func = getattr(fn, "func", None)
    if func is not None:
        return func
    coroutine = getattr(fn, "coroutine", None)
    if coroutine is not None:
        return coroutine
    return fn


async def _run_internal_tool(tc: ToolCall, tool_map: dict, dep_name: str) -> ToolResult:
    start = time.monotonic()
    fn = tool_map.get(tc.tool)
    if fn is None:
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=tc.args,
            output={},
            error=f"unknown tool: {tc.tool}",
            duration_ms=0,
        )
    kwargs = dict(tc.args)
    sig = inspect.signature(_tool_callable(fn))
    if "package_name" in sig.parameters:
        # Force-injected: this nested loop cannot fetch evidence for a
        # different package even if its own LLM tried, mirroring the outer
        # finding_enricher loop's identical guarantee.
        kwargs["package_name"] = dep_name
    try:
        output = (
            await fn.ainvoke(kwargs) if hasattr(fn, "ainvoke") else await fn(**kwargs)
        )
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=kwargs,
            output=output if isinstance(output, dict) else {"result": output},
            error=None,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as exc:
        logger.warning("impact_analysis: tool %s failed: %s", tc.tool, exc)
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=kwargs,
            output={},
            error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _ground_from_tool_results(tool_results: list[ToolResult]) -> dict:
    """Counts/paths/source are taken verbatim from actual tool output, never
    from the nested LLM, so a hallucinated file count can't reach the
    report."""
    for tr in reversed(tool_results):
        if tr.tool == "blast_radius" and not tr.error and tr.output.get("available"):
            return {
                **{k: v for k, v in tr.output.items() if k in _BLAST_RADIUS_FIELDS},
                "available": True,
                "source": "codegraph",
            }
    for tr in reversed(tool_results):
        if tr.tool == "find_usage_sites" and not tr.error:
            # find_usage_sites returns a list[dict], not a dict — _run_internal_tool
            # wraps any non-dict tool return as {"result": <value>} (same convention
            # as the outer _run_tool), so the actual site list lives under "result".
            sites = tr.output.get("result", []) if isinstance(tr.output, dict) else []
            files = sorted(
                {r["file"] for r in sites if isinstance(r, dict) and r.get("file")}
            )
            if files:
                return {
                    "available": True,
                    "affected_file_count": len(files),
                    "affected_files": files,
                    "source": "semantic_search",
                }
    return {"available": False, "source": "unavailable"}


async def analyze_impact(
    finding: FindingNote,
    prep: PrepResult,
    container,
    depth: int = 3,
) -> BlastRadiusSummary:
    tool_map = _build_internal_tool_map(finding, prep, container)
    tool_results: list[ToolResult] = []
    narrative = ""
    use_cases_impacted: list[str] = []

    structured = _llm.with_structured_output(
        ImpactAnalysisDecision, method="function_calling"
    )

    for iteration in range(_MAX_ITERATIONS):
        system = _SYSTEM.format(
            dep_name=finding.dep_name,
            tool_descriptions=_format_internal_tools(tool_map),
            max_iter=_MAX_ITERATIONS,
        )
        prompt = (
            f"Tool results so far:\n{_format_tool_results(tool_results)}\n\n"
            f"Iteration: {iteration + 1}/{_MAX_ITERATIONS}"
        )
        try:
            decision = cast(
                ImpactAnalysisDecision,
                await structured.ainvoke(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ]
                ),
            )
        except AssertionError:
            raise
        except Exception as exc:
            logger.warning(
                "impact_analysis: structured decision failed, retrying: %s", exc
            )
            continue

        last = iteration == _MAX_ITERATIONS - 1
        if decision.finalize or last:
            narrative = decision.narrative
            use_cases_impacted = decision.use_cases_impacted
            break

        if decision.tool_calls:
            new_results = await asyncio.gather(
                *[
                    _run_internal_tool(tc, tool_map, finding.dep_name)
                    for tc in decision.tool_calls
                ]
            )
            tool_results.extend(new_results)

    grounded = _ground_from_tool_results(tool_results)
    return BlastRadiusSummary(
        **grounded,
        narrative=narrative,
        use_cases_impacted=use_cases_impacted,
    )


def make_impact_analysis_tool(finding: FindingNote, prep: PrepResult, container):
    @tool
    async def impact_analysis(depth: int = 3) -> dict:
        """Investigate the real usage of this finding's package: which files
        import it, whether that reaches production code, and which business
        use cases/capabilities are actually affected. Always available."""
        result = await analyze_impact(finding, prep, container, depth)
        return result.model_dump()

    return impact_analysis
