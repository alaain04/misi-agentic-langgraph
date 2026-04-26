"""Background task: run a job through the full analysis pipeline."""

import logging

from langgraph.types import Command

from src.graphs.main_graph import main_graph
from src.graphs.main_graph.constants import (
    ORCHESTRATOR,
    RECOMMENDER,
    REVIEWER,
    SUMMARIZER,
)
from src.models.job import JobStatus
from src.services.job_dao import JobDAO
from src.services.vector_store import delete_store

logger = logging.getLogger(__name__)

_DISCOVERY_OUTPUT_KEYS = {
    "project_metadata",
    "direct_dependencies",
    "transitive_dependencies",
    "dependency_tree",
    "manifest_files",
    "discovery_summary",
    "discovery_error",
}


async def _finalize(dao: JobDAO, job_id: str, result: dict) -> None:
    delete_store(job_id)
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
                "summary": result.get("summary", ""),
                "review": result.get("review", ""),
                "recommendation": result.get("recommendation", ""),
            },
        )


async def _stream_graph(
    graph, input_data, config, dao: JobDAO, job_id: str, on_orchestrator_complete=None
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

            if node_name == "project_discovery":
                await dao.complete_artifact(job_id, "project_discovery", "done")
                await dao.start_artifact(job_id, ORCHESTRATOR)
            elif node_name == ORCHESTRATOR:
                artifact_status = "cancelled" if node_update.get("cancelled") else "done"
                await dao.complete_artifact(job_id, ORCHESTRATOR, artifact_status)
                if on_orchestrator_complete and not node_update.get("cancelled"):
                    await on_orchestrator_complete()
            elif node_name == SUMMARIZER:
                await dao.start_artifact(job_id, SUMMARIZER)
                if "summary" in node_update:
                    await dao.update_artifact_data(job_id, SUMMARIZER, {"output": node_update["summary"]})
                await dao.complete_artifact(job_id, SUMMARIZER, "done")
            elif node_name == REVIEWER:
                await dao.start_artifact(job_id, REVIEWER)
                if "review" in node_update:
                    await dao.update_artifact_data(job_id, REVIEWER, {"output": node_update["review"]})
                await dao.complete_artifact(job_id, REVIEWER, "done")
            elif node_name == RECOMMENDER:
                await dao.start_artifact(job_id, RECOMMENDER)
                if "recommendation" in node_update:
                    await dao.update_artifact_data(job_id, RECOMMENDER, {"output": node_update["recommendation"]})
                await dao.complete_artifact(job_id, RECOMMENDER, "done")

    return interrupt_payload


async def run_analysis(
    job_id: str,
    package_json: str,
    lock_file: str,
    lock_file_name: str,
    concern: str,
) -> None:
    dao = JobDAO()
    await dao.update_status(job_id, JobStatus.running)
    await dao.start_artifact(job_id, "project_discovery")

    config = {"configurable": {"thread_id": job_id}}

    try:
        interrupt_payload = await _stream_graph(
            main_graph,
            {
                "package_json_content": package_json,
                "lock_file_content": lock_file,
                "lock_file_name": lock_file_name,
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
            await dao.save_pending_chat(
                job_id,
                assistant_message=interrupt_payload.get("assistant_message", ""),
                plan=interrupt_payload.get("plan", []),
            )
            return

        snapshot = await main_graph.aget_state(config)
        await _finalize(dao, job_id, snapshot.values)

    except Exception:
        logger.exception("job=%s unhandled error in graph", job_id)
        delete_store(job_id)
        await dao.mark_failed(job_id)


async def resume_analysis(job_id: str, user_message: str) -> None:
    """Resume the orchestrator with a plain-text user message."""
    dao = JobDAO()
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
            # Orchestrator looped and interrupted again (user requested changes)
            await dao.save_pending_chat(
                job_id,
                assistant_message=interrupt_payload.get("assistant_message", ""),
                plan=interrupt_payload.get("plan", []),
            )
            return

        snapshot = await main_graph.aget_state(config)
        await _finalize(dao, job_id, snapshot.values)

    except Exception:
        logger.exception("job=%s unhandled error on resume", job_id)
        delete_store(job_id)
        await dao.mark_failed(job_id)
