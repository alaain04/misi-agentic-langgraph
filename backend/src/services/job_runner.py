"""Background task: run a job through the full analysis pipeline."""

import logging

from langgraph.types import Command

from src.graphs.main_graph import main_graph
from src.models.job import JobStatus
from src.services.job_dao import JobDAO

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
    if result.get("discovery_error"):
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
                "final_report": result.get("final_report", ""),
            },
        )


async def _stream_graph(
    graph, input_data, config, dao: JobDAO, job_id: str
) -> dict | None:
    """Stream graph execution, tracking backbone node artifacts.

    Returns the interrupt payload if the graph paused at a human-in-the-loop
    checkpoint, or None if the graph ran to completion.
    """
    interrupt_payload = None

    async for chunk in graph.astream(input_data, config, stream_mode="updates"):
        for node_name, node_update in chunk.items():
            if node_name == "__interrupt__":
                # node_update is a tuple of Interrupt objects
                interrupt_payload = node_update[0].value
                continue

            if node_name == "project_discovery":
                await dao.complete_artifact(job_id, "project_discovery", "done")
                await dao.start_artifact(job_id, "planner")
            elif node_name == "planner":
                await dao.complete_artifact(job_id, "planner", "done")
            elif node_name == "final_report":
                await dao.start_artifact(job_id, "final_report")
                await dao.complete_artifact(job_id, "final_report", "done")

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
            },
            config,
            dao,
            job_id,
        )

        if interrupt_payload is not None:
            snapshot = await main_graph.aget_state(config)
            state = snapshot.values
            await dao.save_pending_plan(
                job_id,
                {
                    "discovery": {
                        k: state.get(k) for k in _DISCOVERY_OUTPUT_KEYS if k in state
                    },
                    "plan": interrupt_payload.get("plan", []),
                    "discovery_summary": interrupt_payload.get("discovery_summary", ""),
                    "direct_dependencies_count": interrupt_payload.get(
                        "direct_dependencies_count", 0
                    ),
                },
            )
            return

        snapshot = await main_graph.aget_state(config)
        await _finalize(dao, job_id, snapshot.values)

    except Exception:
        logger.exception("job=%s unhandled error in graph", job_id)
        await dao.mark_failed(job_id)


async def resume_analysis(job_id: str, decision: dict) -> None:
    dao = JobDAO()
    await dao.update_status(job_id, JobStatus.running)

    config = {"configurable": {"thread_id": job_id}}

    try:
        interrupt_payload = await _stream_graph(
            main_graph,
            Command(resume=decision),
            config,
            dao,
            job_id,
        )

        if interrupt_payload is not None:
            # "refine" loop: another interrupt, save updated plan for re-approval
            snapshot = await main_graph.aget_state(config)
            state = snapshot.values
            await dao.save_pending_plan(
                job_id,
                {
                    "discovery": {
                        k: state.get(k) for k in _DISCOVERY_OUTPUT_KEYS if k in state
                    },
                    "plan": interrupt_payload.get("plan", []),
                    "discovery_summary": interrupt_payload.get("discovery_summary", ""),
                    "direct_dependencies_count": interrupt_payload.get(
                        "direct_dependencies_count", 0
                    ),
                },
            )
            return

        snapshot = await main_graph.aget_state(config)
        await _finalize(dao, job_id, snapshot.values)

    except Exception:
        logger.exception("job=%s unhandled error on resume", job_id)
        await dao.mark_failed(job_id)
