import logging
import uuid

from domain.ports.job_repository_port import JobRepositoryPort
from domain.ports.messaging_port import MessagingPort

logger = logging.getLogger(__name__)


class IngestService:
    def __init__(
        self,
        job_repo: JobRepositoryPort,
        messaging: MessagingPort,
        subject_prefix: str,
    ) -> None:
        self._job_repo = job_repo
        self._messaging = messaging
        self._subject_prefix = subject_prefix

    async def create_job(self, entity_type: str, items: list[str]) -> str:
        job_id = str(uuid.uuid4())
        await self._job_repo.create(job_id, items)
        subject = f"{self._subject_prefix}.{entity_type}"
        try:
            for name in items:
                await self._messaging.publish(
                    subject,
                    {"job_id": job_id, "entity_type": entity_type, "name": name},
                )
        except Exception:
            await self._job_repo.delete(job_id)
            raise
        logger.info("ingest: created job %s (%d items)", job_id, len(items))
        return job_id

    async def get_status(self, job_id: str) -> dict[str, object] | None:
        return await self._job_repo.get_status(job_id)
