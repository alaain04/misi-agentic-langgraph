"""Background task: run a job through the full analysis pipeline."""

import dataclasses
import logging
import shutil

from langgraph.types import Command

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph import main_graph
from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter
from src.main_graph.adapters.langchain_vector_store_adapter import LangchainVectorStoreAdapter
from src.main_graph.constants import (
    EVIDENCE_COLLECTOR,
    EVIDENCE_CORRELATOR,
    FINDING_REVIEWER,
    INVESTIGATION_PLANNER,
    REPORT_BUILDER,
)
from src.main_graph.subgraphs.discovery.dao import sbom_dao
from src.main_graph.subgraphs.discovery.tools.docker import make_docker_tool
from src.models.job import JobStatus
from src.services.vector_store import delete_store, get_or_create_store

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
    elif result.get("discovery_error") or result.get("sbom_error"):
        error = result.get("discovery_error") or result.get("sbom_error")
        logger.error("job=%s error=%s", job_id, error)
        await dao.mark_failed(job_id, error=error)
    else:
        logger.info("job=%s done", job_id)
        await dao.save_result(
            job_id,
            {
                "discovery": {
                    k: result[k] for k in _DISCOVERY_OUTPUT_KEYS if k in result
                },
                "risk_findings": [dataclasses.asdict(f) for f in result.get("risk_findings") or []],
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
) -> dict | None:
    """Stream graph execution, tracking backbone node artifacts.

    Returns the interrupt payload if the graph paused, or None if run to completion.
    """
    interrupt_payload = None
    _skill_tasks: list[str] = []

    async for chunk in graph.astream(input_data, config, stream_mode="updates"):
        for node_name, node_update in chunk.items():
            if node_name == "__interrupt__":
                interrupt_payload = node_update[0].value
                logger.info("job=%s node=%s interrupted (awaiting user input)", job_id, node_name)
                continue

            logger.info("job=%s node=%s completed", job_id, node_name)

            if node_name == "skill_executor":
                _skill_tasks.extend(node_update.get("executed_skill_tasks", []))
            elif node_name == "discovery":
                steps = node_update.get("discovery_steps", [])
                if steps:
                    await dao.update_artifact_data(job_id, "discovery", {"steps": steps})
                if node_update.get("discovery_error") or node_update.get("sbom_error"):
                    await dao.complete_artifact(job_id, "discovery", "failed")
                else:
                    await dao.complete_artifact(job_id, "discovery", "done")
                    await dao.start_artifact(job_id, INVESTIGATION_PLANNER)
            elif node_name == INVESTIGATION_PLANNER:
                await dao.complete_artifact(job_id, INVESTIGATION_PLANNER, "done")
            elif node_name == EVIDENCE_COLLECTOR:
                await dao.start_artifact(job_id, EVIDENCE_COLLECTOR)
                if _skill_tasks:
                    await dao.update_artifact_data(job_id, EVIDENCE_COLLECTOR, {"steps": _skill_tasks})
                await dao.complete_artifact(job_id, EVIDENCE_COLLECTOR, "done")
            elif node_name == EVIDENCE_CORRELATOR:
                await dao.start_artifact(job_id, EVIDENCE_CORRELATOR)
                findings = node_update.get("risk_findings") or []
                contradictions = node_update.get("contradictions") or []
                await dao.update_artifact_data(job_id, EVIDENCE_CORRELATOR, {
                    "data": {
                        "findings_count": len(findings),
                        "contradictions_count": len(contradictions),
                        "deps_covered": [f.dep_name for f in findings],
                    }
                })
                await dao.complete_artifact(job_id, EVIDENCE_CORRELATOR, "done")
            elif node_name == FINDING_REVIEWER:
                await dao.start_artifact(job_id, FINDING_REVIEWER)
                if "review_approved" in node_update:
                    await dao.update_artifact_data(
                        job_id,
                        FINDING_REVIEWER,
                        {
                            "output": {
                                "review_approved": node_update.get("review_approved"),
                                "reviewer_feedback": node_update.get("reviewer_feedback"),
                            }
                        },
                    )
                await dao.complete_artifact(job_id, FINDING_REVIEWER, "done")
            elif node_name == REPORT_BUILDER:
                await dao.start_artifact(job_id, REPORT_BUILDER)
                if "analysis_report" in node_update:
                    await dao.update_artifact_data(
                        job_id,
                        REPORT_BUILDER,
                        {"output": node_update["analysis_report"]},
                    )
                await dao.complete_artifact(job_id, REPORT_BUILDER, "done")

    return interrupt_payload


def _build_config(job_id: str, dao: JobRepositoryPort) -> dict:
    container = DockerContainerAdapter()
    store = get_or_create_store(job_id)
    return {
        "configurable": {
            "thread_id": job_id,
            "job_repo": dao,
            "vector_store": LangchainVectorStoreAdapter(store),
            "container": container,
            "docker_tool": make_docker_tool(container),
            "sbom_dao": sbom_dao,
        }
    }


async def run_analysis(
    job_id: str,
    repo_url: str,
    concern: str,
    dao: JobRepositoryPort,
) -> None:
    await dao.update_status(job_id, JobStatus.running)
    await dao.start_artifact(job_id, "discovery")

    config = _build_config(job_id, dao)

    try:
        interrupt_payload = await _stream_graph(
            main_graph,
            {
                "repo_url": repo_url,
                "concern": concern,
                "job_id": job_id,
                "messages": [],
                "evidence": [],
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

    except Exception as exc:
        logger.exception("job=%s unhandled error in graph", job_id)
        delete_store(job_id)
        await dao.mark_failed(job_id, error=str(exc))


async def resume_analysis(
    job_id: str,
    user_message: str,
    dao: JobRepositoryPort,
) -> None:
    """Resume graph execution after a human-in-the-loop interrupt."""
    await dao.update_status(job_id, JobStatus.processing)

    config = _build_config(job_id, dao)

    try:
        interrupt_payload = await _stream_graph(
            main_graph,
            Command(resume=user_message),
            config,
            dao,
            job_id,
        )

        if interrupt_payload is not None:
            await dao.update_status(job_id, JobStatus.awaiting_approval)
            return

        snapshot = await main_graph.aget_state(config)
        await _finalize(dao, job_id, snapshot.values)

    except Exception as exc:
        logger.exception("job=%s unhandled error on resume", job_id)
        delete_store(job_id)
        await dao.mark_failed(job_id, error=str(exc))
