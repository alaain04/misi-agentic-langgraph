"""Background task: run a job through the 3-layer pipeline."""

from __future__ import annotations

import logging
import shutil

from langgraph.types import Command

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph import main_graph
from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter
from src.main_graph.adapters.gh_cli_adapter import GhCliAdapter
from src.main_graph.constants import ANALYSIS, PREP, REPORT
from src.main_graph.subgraphs.discovery.tools.docker import make_docker_tool
from src.main_graph.tools.external_api import clear_cache
from src.models.job import JobStatus
from src.services.dependencies import get_input_cache, get_result_dao
from src.utils.cost import CostCallback

logger = logging.getLogger(__name__)


def _build_config(
    job_id: str,
    dao: JobRepositoryPort,
    cost_cb: CostCallback,
    github_token: str | None = None,
    remediate: bool = False,
) -> dict:
    container = DockerContainerAdapter()
    configurable = {
        "thread_id": job_id,
        "job_repo": dao,
        "container": container,
        "docker_tool": make_docker_tool(container),
        "result_dao": get_result_dao(),
        "input_cache": get_input_cache(),
        "remediate": remediate,
        "git_pr": GhCliAdapter(),
    }
    if github_token:
        configurable["github_token"] = github_token
    return {
        "configurable": configurable,
        "callbacks": [cost_cb],
    }


async def _stream_graph(
    graph,
    input_data,
    config,
    dao: JobRepositoryPort,
    job_id: str,
    cost_cb: CostCallback,
) -> None:
    prev_cost = 0.0
    async for chunk in graph.astream(input_data, config, stream_mode="updates"):
        for node_name, node_update in chunk.items():
            logger.info("job=%s node=%s completed", job_id, node_name)

            if node_name in (PREP, ANALYSIS, REPORT):
                cost_now = cost_cb.cost()
                await dao.update_artifact_data(
                    job_id, node_name, {"cost": round(cost_now - prev_cost, 6)}
                )
                prev_cost = cost_now

            if node_name == PREP:
                status = "failed" if node_update.get("discovery_error") else "done"
                await dao.complete_artifact(job_id, PREP, status)
                if status == "done":
                    await dao.start_artifact(job_id, ANALYSIS)

            elif node_name == ANALYSIS:
                await dao.complete_artifact(job_id, ANALYSIS, "done")
                if node_update.get("analysis_result_id"):
                    await dao.start_artifact(job_id, REPORT)

            elif node_name == REPORT:
                report_result_id = node_update.get("report_result_id")
                await dao.complete_artifact(job_id, REPORT, "done")
                if report_result_id:
                    result_dao = get_result_dao()
                    report = await result_dao.get_report(report_result_id)
                    await dao.update_artifact_data(
                        job_id, REPORT, {"output": report.model_dump()}
                    )


async def _finalize(dao: JobRepositoryPort, job_id: str, config: dict) -> None:
    clear_cache()
    snapshot = await main_graph.aget_state(config)
    values = snapshot.values

    if prep_result_id := values.get("prep_result_id"):
        result_dao = get_result_dao()
        try:
            prep = await result_dao.get_prep(prep_result_id)
            if prep.repo_path:
                shutil.rmtree(prep.repo_path, ignore_errors=True)
        except Exception:
            pass

    if values.get("cancelled"):
        await dao.mark_cancelled(job_id)
    elif values.get("discovery_error") or not values.get("prep_result_id"):
        await dao.mark_failed(
            job_id, error=values.get("discovery_error", "prep failed")
        )
    else:
        report_result_id = values.get("report_result_id", "")
        await dao.save_result(job_id, {"report_result_id": report_result_id})


async def run_analysis(
    job_id: str,
    repo_url: str,
    concern: str,
    autopilot: bool,
    dao: JobRepositoryPort,
    github_token: str | None = None,
    remediate: bool = False,
) -> None:
    await dao.update_status(job_id, JobStatus.running)
    await dao.start_artifact(job_id, PREP)
    cost_cb = CostCallback()
    config = _build_config(job_id, dao, cost_cb, github_token=github_token,
                           remediate=remediate)
    clear_cache()

    try:
        await _stream_graph(
            main_graph,
            {
                "repo_url": repo_url,
                "concern": concern,
                "job_id": job_id,
                "autopilot": autopilot,
                "messages": [],
            },
            config,
            dao,
            job_id,
            cost_cb,
        )
        await dao.save_cost(job_id, cost_cb.cost())
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
        await _stream_graph(
            main_graph,
            Command(resume=user_message),
            config,
            dao,
            job_id,
            cost_cb,
        )
        await dao.save_cost(job_id, cost_cb.cost())
        await _finalize(dao, job_id, config)
        await dao.update_status(job_id, JobStatus.done)

    except Exception as exc:
        logger.exception("job=%s unhandled error on resume", job_id)
        clear_cache()
        await dao.save_cost(job_id, cost_cb.cost())
        await dao.mark_failed(job_id, error=str(exc))
