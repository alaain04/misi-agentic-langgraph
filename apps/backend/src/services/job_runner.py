"""Background task: run a job through the ReAct conductor pipeline."""
from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime

from langgraph.types import Command

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph import main_graph
from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter
from src.main_graph.constants import CONDUCTOR, HITL_GATE, PREP, REPORT_BUILDER, TOOL_RUNNER
from src.main_graph.subgraphs.discovery.tools.docker import make_docker_tool
from src.main_graph.tools.external_api import clear_cache
from src.models.job import JobStatus
from src.utils.cost import CostCallback

logger = logging.getLogger(__name__)


def _build_config(job_id: str, dao: JobRepositoryPort, cost_cb: CostCallback) -> dict:
    container = DockerContainerAdapter()
    return {
        "configurable": {
            "thread_id": job_id,
            "job_repo": dao,
            "container": container,
            "docker_tool": make_docker_tool(container),
        },
        "callbacks": [cost_cb],
    }


async def _stream_graph(graph, input_data, config, dao: JobRepositoryPort, job_id: str) -> bool:
    """Stream graph updates and track artifacts. Returns True if interrupted."""
    interrupted = False
    current_conductor_iteration = 0

    async for chunk in graph.astream(input_data, config, stream_mode="updates"):
        for node_name, node_update in chunk.items():
            if node_name == "__interrupt__":
                interrupted = True
                for intr in node_update:
                    val = intr.value
                    await dao.start_artifact(job_id, HITL_GATE)
                    await dao.push_artifact_message(job_id, HITL_GATE, {
                        "role": "assistant",
                        "content": val.get("question", ""),
                        "created_at": datetime.now(UTC).isoformat(),
                        "type": val.get("type", "checkpoint"),
                    })
                continue

            logger.info("job=%s node=%s completed", job_id, node_name)

            if node_name == PREP:
                if node_update.get("discovery_error"):
                    await dao.complete_artifact(job_id, PREP, "failed")
                else:
                    await dao.complete_artifact(job_id, PREP, "done")
                    await dao.start_artifact(job_id, CONDUCTOR)

            elif node_name == CONDUCTOR:
                current_conductor_iteration = node_update.get("conductor_iteration") or 0
                decision = node_update.get("conductor_decision")
                if decision:
                    await dao.push_artifact_item(job_id, CONDUCTOR, "iterations", {
                        "iteration": current_conductor_iteration,
                        "tool_calls": [tc.model_dump() for tc in decision.tool_calls],
                        "findings_count": len(node_update.get("findings") or []),
                        "finalize": decision.finalize,
                        "reasoning": decision.reasoning,
                        "started_at": datetime.now(UTC).isoformat(),
                    })

            elif node_name == TOOL_RUNNER:
                await dao.start_artifact(job_id, TOOL_RUNNER)
                results = node_update.get("tool_results") or []
                await dao.push_artifact_item(job_id, TOOL_RUNNER, "iterations", {
                    "conductor_iteration": current_conductor_iteration,
                    "tools_run": [tr.tool for tr in results],
                    "errors": [{"tool": tr.tool, "error": tr.error} for tr in results if tr.error],
                    "started_at": datetime.now(UTC).isoformat(),
                })

            elif node_name == HITL_GATE:
                await dao.complete_artifact(job_id, HITL_GATE, "done")

            elif node_name == REPORT_BUILDER:
                await dao.start_artifact(job_id, REPORT_BUILDER)
                if "analysis_report" in node_update:
                    await dao.update_artifact_data(job_id, REPORT_BUILDER, {
                        "output": node_update["analysis_report"]
                    })
                await dao.complete_artifact(job_id, REPORT_BUILDER, "done")

    return interrupted


async def _finalize(dao: JobRepositoryPort, job_id: str, config: dict) -> None:
    clear_cache()
    snapshot = await main_graph.aget_state(config)
    values = snapshot.values
    if repo_path := values.get("repo_path"):
        shutil.rmtree(repo_path, ignore_errors=True)
    if values.get("cancelled"):
        await dao.mark_cancelled(job_id)
    elif values.get("discovery_error"):
        await dao.mark_failed(job_id, error=values["discovery_error"])
    else:
        await dao.save_result(job_id, {"analysis_report": values.get("analysis_report")})


async def run_analysis(
    job_id: str,
    repo_url: str,
    concern: str,
    autopilot: bool,
    dao: JobRepositoryPort,
) -> None:
    await dao.update_status(job_id, JobStatus.running)
    await dao.start_artifact(job_id, PREP)
    cost_cb = CostCallback()
    config = _build_config(job_id, dao, cost_cb)
    clear_cache()

    try:
        interrupted = await _stream_graph(
            main_graph,
            {
                "repo_url": repo_url,
                "concern": concern,
                "job_id": job_id,
                "autopilot": autopilot,
                "messages": [],
                "tool_results": [],
                "findings": [],
            },
            config,
            dao,
            job_id,
        )
        await dao.save_cost(job_id, cost_cb.cost())
        if interrupted:
            await dao.update_status(job_id, JobStatus.awaiting_approval)
            return
        await _finalize(dao, job_id, config)
        await dao.update_status(job_id, JobStatus.done)

    except Exception as exc:
        logger.exception("job=%s unhandled error", job_id)
        clear_cache()
        await dao.save_cost(job_id, cost_cb.cost())
        await dao.mark_failed(job_id, error=str(exc))


async def resume_analysis(
    job_id: str,
    user_message: str,
    dao: JobRepositoryPort,
) -> None:
    await dao.update_status(job_id, JobStatus.processing)
    cost_cb = CostCallback()
    config = _build_config(job_id, dao, cost_cb)

    try:
        interrupted = await _stream_graph(
            main_graph,
            Command(resume=user_message),
            config,
            dao,
            job_id,
        )
        await dao.save_cost(job_id, cost_cb.cost())
        if interrupted:
            await dao.update_status(job_id, JobStatus.awaiting_approval)
            return
        await _finalize(dao, job_id, config)
        await dao.update_status(job_id, JobStatus.done)

    except Exception as exc:
        logger.exception("job=%s unhandled error on resume", job_id)
        clear_cache()
        await dao.save_cost(job_id, cost_cb.cost())
        await dao.mark_failed(job_id, error=str(exc))
