"""Background task: run a job through the analysis pipeline."""
import logging
import shutil

from langgraph.types import Command

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph import main_graph
from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter
from src.main_graph.subgraphs.discovery.tools.docker import make_docker_tool
from src.models.job import JobStatus

logger = logging.getLogger(__name__)


def _build_config(job_id: str, dao: JobRepositoryPort) -> dict:
    container = DockerContainerAdapter()
    return {
        "configurable": {
            "thread_id": job_id,
            "job_repo": dao,
            "container": container,
            "docker_tool": make_docker_tool(container),
        }
    }


async def run_analysis(
    job_id: str,
    repo_url: str,
    concern: str,
    dao: JobRepositoryPort,
    autopilot: bool = False,
) -> None:
    await dao.update_status(job_id, JobStatus.running)
    config = _build_config(job_id, dao)
    try:
        async for _ in main_graph.astream(
            {"repo_url": repo_url, "concern": concern, "job_id": job_id,
             "autopilot": autopilot, "messages": [], "tool_results": [], "findings": []},
            config,
            stream_mode="updates",
        ):
            pass
        snapshot = await main_graph.aget_state(config)
        if snapshot.values.get("cancelled"):
            await dao.mark_cancelled(job_id)
        elif snapshot.values.get("discovery_error"):
            await dao.mark_failed(job_id, error=snapshot.values["discovery_error"])
        else:
            await dao.save_result(job_id, {"analysis_report": snapshot.values.get("analysis_report")})
    except Exception as exc:
        logger.exception("job=%s unhandled error", job_id)
        await dao.mark_failed(job_id, error=str(exc))
    finally:
        if repo_path := (await main_graph.aget_state(config)).values.get("repo_path"):
            shutil.rmtree(repo_path, ignore_errors=True)


async def resume_analysis(
    job_id: str,
    user_message: str,
    dao: JobRepositoryPort,
) -> None:
    await dao.update_status(job_id, JobStatus.processing)
    config = _build_config(job_id, dao)
    try:
        async for _ in main_graph.astream(Command(resume=user_message), config, stream_mode="updates"):
            pass
        snapshot = await main_graph.aget_state(config)
        if snapshot.values.get("cancelled"):
            await dao.mark_cancelled(job_id)
        else:
            await dao.save_result(job_id, {"analysis_report": snapshot.values.get("analysis_report")})
    except Exception as exc:
        logger.exception("job=%s unhandled error on resume", job_id)
        await dao.mark_failed(job_id, error=str(exc))
