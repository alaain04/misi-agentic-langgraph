"""Background task: run a job through the full analysis pipeline."""

import logging
import shutil

from langgraph.types import Command

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph import main_graph
from src.main_graph.constants import (
    CROSS_ANALYZER,
    ORCHESTRATOR,
    REPORT_REVIEWER,
)
from src.models.job import JobStatus
from src.services.vector_store import delete_store

logger = logging.getLogger(__name__)

_DISCOVERY_OUTPUT_KEYS = {
    "project_metadata",
    "manifest_files",
    "discovery_summary",
    "discovery_error",
    "sbom_result_id",
    "sbom_error",
}


async def _finalize(dao: JobRepositoryPort, job_id: str, result: dict) -> None:
    delete_store(job_id)
    if repo_path := result.get("repo_path"):
        shutil.rmtree(repo_path, ignore_errors=True)
    if result.get("cancelled"):
        logger.info("job=%s cancelled by user", job_id)
        await dao.mark_cancelled(job_id)
    elif result.get("discovery_error"):
        logger.error("job=%s error=%s", job_id, result["discovery_error"])
        await dao.mark_failed(job_id)
    else:
        logger.info(
            "job=%s done subgraphs=%s",
            job_id,
            [r.get("subgraph") for r in result.get("subgraph_results", [])],
        )
        await dao.save_result(
            job_id,
            {
                "discovery": {
                    k: result[k] for k in _DISCOVERY_OUTPUT_KEYS if k in result
                },
                "plan": result.get("plan", []),
                "subgraph_results": result.get("subgraph_results", []),
                "analysis_report": result.get("analysis_report"),
                "review_approved": result.get("review_approved"),
                "review_iterations": result.get("review_iterations"),
            },
        )


async def _stream_graph(
    graph,
    input_data,
    config,
    dao: JobRepositoryPort,
    job_id: str,
    on_orchestrator_complete=None,
) -> dict | None:
    """Stream graph execution, tracking backbone node artifacts.

    Returns the interrupt payload if the graph paused at the orchestrator,
    or None if the graph ran to completion.
    """
    interrupt_payload = None

    async for chunk in graph.astream(input_data, config, stream_mode="updates"):
        for node_name, node_update in chunk.items():
            if node_name == "__interrupt__":
                interrupt_payload = node_update[0].value
                continue

            if node_name == "discovery":
                await dao.complete_artifact(job_id, "discovery", "done")
                await dao.start_artifact(job_id, ORCHESTRATOR)
            elif node_name == ORCHESTRATOR:
                artifact_status = (
                    "cancelled" if node_update.get("cancelled") else "done"
                )
                await dao.complete_artifact(job_id, ORCHESTRATOR, artifact_status)
                if on_orchestrator_complete and not node_update.get("cancelled"):
                    await on_orchestrator_complete()
            elif node_name == CROSS_ANALYZER:
                await dao.start_artifact(job_id, CROSS_ANALYZER)
                if "analysis_report" in node_update:
                    await dao.update_artifact_data(
                        job_id,
                        CROSS_ANALYZER,
                        {"output": node_update["analysis_report"]},
                    )
                await dao.complete_artifact(job_id, CROSS_ANALYZER, "done")
            elif node_name == REPORT_REVIEWER:
                await dao.start_artifact(job_id, REPORT_REVIEWER)
                if "review_approved" in node_update:
                    await dao.update_artifact_data(
                        job_id,
                        REPORT_REVIEWER,
                        {
                            "output": {
                                "review_approved": node_update.get("review_approved"),
                                "reviewer_feedback": node_update.get(
                                    "reviewer_feedback"
                                ),
                            }
                        },
                    )
                await dao.complete_artifact(job_id, REPORT_REVIEWER, "done")

    return interrupt_payload


async def run_analysis(
    job_id: str,
    repo_url: str,
    concern: str,
    dao: JobRepositoryPort,
) -> None:
    await dao.update_status(job_id, JobStatus.running)
    await dao.start_artifact(job_id, "discovery")

    config = {"configurable": {"thread_id": job_id}}

    try:
        interrupt_payload = await _stream_graph(
            main_graph,
            {
                "repo_url": repo_url,
                "concern": concern,
                "job_id": job_id,
                "subgraph_results": [],
                "messages": [],
            },
            config,
            dao,
            job_id,
        )

        if interrupt_payload is not None:
            await dao.update_status(job_id, JobStatus.awaiting_approval)
            return

        snapshot = await main_graph.aget_state(config)
        await _finalize(dao, job_id, snapshot.values)

    except Exception:
        logger.exception("job=%s unhandled error in graph", job_id)
        delete_store(job_id)
        await dao.mark_failed(job_id)


async def resume_analysis(
    job_id: str,
    user_message: str,
    dao: JobRepositoryPort,
) -> None:
    """Resume the orchestrator with a plain-text user message."""
    await dao.update_status(job_id, JobStatus.processing)

    config = {"configurable": {"thread_id": job_id}}

    async def _on_approved() -> None:
        await dao.update_status(job_id, JobStatus.running)

    try:
        interrupt_payload = await _stream_graph(
            main_graph,
            Command(resume=user_message),
            config,
            dao,
            job_id,
            on_orchestrator_complete=_on_approved,
        )

        if interrupt_payload is not None:
            await dao.update_status(job_id, JobStatus.awaiting_approval)
            return

        snapshot = await main_graph.aget_state(config)
        await _finalize(dao, job_id, snapshot.values)

    except Exception:
        logger.exception("job=%s unhandled error on resume", job_id)
        delete_store(job_id)
        await dao.mark_failed(job_id)
