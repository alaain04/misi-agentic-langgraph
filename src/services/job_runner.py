"""Background task: run a job through the ProjectDiscovery subgraph."""

import logging

from src.graphs.project_discovery import project_discovery_subgraph
from src.models.job import JobStatus
from src.services.job_dao import JobDAO

logger = logging.getLogger(__name__)


async def run_discovery(
    job_id: str,
    repo_url: str,
    concern: str,
    token: str | None,
) -> None:
    dao = JobDAO()
    await dao.update_status(job_id, JobStatus.running)

    try:
        result = await project_discovery_subgraph.ainvoke(
            {"repo_url": repo_url, "concern": concern, "token": token}
        )

        if result.get("discovery_error"):
            logger.error("job=%s error=%s", job_id, result["discovery_error"])
            await dao.update_status(job_id, JobStatus.failed)
        else:
            logger.info("job=%s done summary=%s", job_id, result["discovery_summary"])
            await dao.save_result(job_id, result)

    except Exception:
        logger.exception("job=%s unhandled error in graph", job_id)
        await dao.update_status(job_id, JobStatus.failed)
