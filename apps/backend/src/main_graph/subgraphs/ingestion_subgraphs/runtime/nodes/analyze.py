"""Analyze node for the Runtime subgraph.

Downloads the primary dependency's source code, identifies quality scripts
from package.json, executes them inside Docker, and maps the results to
TestResult / LintResult domain models.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime

from langchain_core.messages import ToolMessage

from src.main_graph.subgraphs.ingestion_subgraphs.runtime.dao import (
    runtime_cache_dao,
    runtime_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.models import (
    LintResult,
    RuntimeCacheEntry,
    RuntimeEntry,
    TestResult,
)
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.state import RuntimeState
from src.main_graph.subgraphs.ingestion_subgraphs.sbom_utils import (
    get_component_version,
    get_vcs_url,
)
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.tools.dependency_tools import (  # noqa: E501
    clone_github_repo,
    read_package_json,
)
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.tools.docker_tools import (
    run_docker_install,
    run_docker_script,
)
from src.utils.config import settings
from src.utils.llm import Model, get_llm, parse_llm_json

_log = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_DOWNLOAD_TOOLS = [clone_github_repo]
_IDENTIFY_TOOLS = [read_package_json]
_EXECUTE_TOOLS = [run_docker_install, run_docker_script]

_DOWNLOAD_PROMPT = """\
You are a tool-calling agent that downloads npm package source code from GitHub.
Given a package name, version, and repository URL, call clone_github_repo with
the repository URL, version, and destination directory.
Return a JSON object with keys: resolved_version, error.
If cloning fails, set error to the error message and resolved_version to null.\
"""

_IDENTIFY_PROMPT = """\
You are a code quality script classifier for npm packages.
Given a package directory, read package.json and identify AT MOST 3 quality scripts.

INCLUDE: test, test:*, tests, audit, check, check:*, validate, verify
EXCLUDE: start, serve, dev, watch, build, bundle, compile, deploy, publish,
         install, docs, storybook, format, fmt, lint, typecheck, tsc

Return ONLY a JSON object: { "quality_scripts": ["name1", "name2"] }\
"""

_EXECUTE_PROMPT = """\
You are a tool-calling agent that executes npm scripts inside Docker containers.
1. Call run_docker_install once to install dependencies.
2. For each script, call run_docker_script to execute it.
   Continue to the next script even if one fails.

Return a JSON object:
{
  "results": {
    "script-name": {
      "script_name": "script-name",
      "exit_code": 0, "stdout": "...", "stderr": "...",
      "duration_seconds": 1.23, "timed_out": false
    }
  },
  "error": null
}\
"""


@dataclass
class _ScriptResult:
    script_name: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = field(default=False)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


async def _run_agent(
    llm_with_tools, tools, system_prompt: str, user_msg: str, max_turns: int
) -> dict | None:
    tool_map = {t.name: t for t in tools}
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]
    for _ in range(max_turns):
        response = await llm_with_tools.ainvoke(messages)
        messages.append(response)
        if not response.tool_calls:
            try:
                return parse_llm_json(response.content or "")
            except Exception:
                return None
        for tc in response.tool_calls:
            tool_fn = tool_map.get(tc["name"])
            if tool_fn:
                # Docker tools are sync; clone tool is async
                if hasattr(tool_fn, "ainvoke"):
                    tool_result = await tool_fn.ainvoke(tc["args"])
                else:
                    tool_result = tool_fn.invoke(tc["args"])
                messages.append(
                    ToolMessage(content=str(tool_result), tool_call_id=tc["id"])
                )
    return None


def _map_to_runtime_entry(
    results: dict[str, _ScriptResult],
) -> tuple[TestResult | None, LintResult | None]:
    test_scripts = [
        r for name, r in results.items() if name.startswith("test") or name == "tests"
    ]
    lint_scripts = [r for name, r in results.items() if name.startswith("lint")]

    test_result: TestResult | None = None
    if test_scripts:
        passed = sum(1 for r in test_scripts if r.passed)
        failed = len(test_scripts) - passed
        errors = [r.stderr[:300] for r in test_scripts if not r.passed and r.stderr]
        test_result = TestResult(passed=passed, failed=failed, errors=errors)

    lint_result: LintResult | None = None
    if lint_scripts:
        errors_count = sum(1 for r in lint_scripts if not r.passed)
        lint_result = LintResult(errors=errors_count)

    return test_result, lint_result


async def analyze(state: RuntimeState) -> dict:
    dep_name = state.get("dependency_name", "")
    if not dep_name:
        result_id = await runtime_dao.save(RuntimeEntry())
        return {"result_id": result_id}

    sbom = state.get("sbom_cyclonedx", {})
    version_spec = (get_component_version(sbom, dep_name) or "").lstrip("^~>=< ")
    repository_url = get_vcs_url(sbom, dep_name) or ""

    if not repository_url:
        _log.warning("runtime.analyze: no repository_url — saving empty entry")
        result_id = await runtime_dao.save(RuntimeEntry())
        return {"result_id": result_id}

    # Cache check — skip Docker + LLM work if this exact version was already analyzed
    cached = await runtime_cache_dao.find_cached_entry(
        dep_name, version_spec, settings.runtime_cache_max_age_days
    )
    if cached is not None:
        result_id = await runtime_dao.save(cached.entry)
        _log.info(
            "runtime.analyze: cache hit for %s@%s, result_id=%s",
            dep_name,
            version_spec,
            result_id,
        )
        return {"result_id": result_id}

    base_tmp = os.path.expanduser("~/tmp")
    os.makedirs(base_tmp, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="npm_qa_", dir=base_tmp)

    try:
        download_llm = _llm.bind_tools(_DOWNLOAD_TOOLS)
        identify_llm = _llm.bind_tools(_IDENTIFY_TOOLS)
        execute_llm = _llm.bind_tools(_EXECUTE_TOOLS)

        # 1. Download
        download_result = await _run_agent(
            download_llm,
            _DOWNLOAD_TOOLS,
            _DOWNLOAD_PROMPT,
            (
                f"Download '{dep_name}' version '{version_spec}'"
                f" from '{repository_url}' into '{tmp_dir}'."
            ),
            max_turns=6,
        )
        if not download_result or download_result.get("error"):
            err = (download_result or {}).get(
                "error", "download agent did not converge"
            )
            _log.warning("runtime.analyze: download failed: %s", err)
            result_id = await runtime_dao.save(RuntimeEntry())
            return {"result_id": result_id}

        # 2. Identify quality scripts
        identify_result = await _run_agent(
            identify_llm,
            _IDENTIFY_TOOLS,
            _IDENTIFY_PROMPT,
            f"Read package.json from '{tmp_dir}' and identify quality scripts.",
            max_turns=4,
        )
        script_names: list[str] = (identify_result or {}).get("quality_scripts", [])

        if not script_names:
            _log.info("runtime.analyze: no quality scripts identified")
            result_id = await runtime_dao.save(RuntimeEntry())
            return {"result_id": result_id}

        script_list = ", ".join(f"'{s}'" for s in script_names)
        # 3. Execute scripts
        execute_result = await _run_agent(
            execute_llm,
            _EXECUTE_TOOLS,
            _EXECUTE_PROMPT,
            (
                f"Package directory: '{tmp_dir}'.\n"
                f"Quality scripts to run: [{script_list}].\n"
                f"Docker image: '{settings.node_docker_image}', "
                f"memory: '{settings.docker_memory_limit}', "
                f"cpu: {settings.docker_cpu_limit}, "
                f"timeout per script: {settings.script_timeout_seconds}s."
            ),
            max_turns=len(script_names) + 6,
        )

        results: dict[str, _ScriptResult] = {}
        if execute_result:
            for name, raw in (execute_result.get("results") or {}).items():
                results[name] = _ScriptResult(
                    script_name=name,
                    exit_code=raw.get("exit_code", -1),
                    stdout=raw.get("stdout", ""),
                    stderr=raw.get("stderr", ""),
                    duration_seconds=raw.get("duration_seconds", 0.0),
                    timed_out=raw.get("timed_out", False),
                )

        test_result, lint_result = _map_to_runtime_entry(results)
        entry = RuntimeEntry(test_results=test_result, lint_results=lint_result)

        try:
            await runtime_cache_dao.upsert_cached_entry(
                RuntimeCacheEntry(
                    dep_name=dep_name,
                    package_version=version_spec,
                    fetched_at=datetime.now(UTC),
                    entry=entry,
                )
            )
        except Exception:
            _log.warning(
                "runtime.analyze: cache write failed for %s@%s",
                dep_name,
                version_spec,
            )

        result_id = await runtime_dao.save(entry)
        _log.info(
            "runtime.analyze: saved result_id=%s scripts_run=%d",
            result_id,
            len(results),
        )
        return {"result_id": result_id}

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
