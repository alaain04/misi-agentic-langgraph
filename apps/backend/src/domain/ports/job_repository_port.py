from abc import ABC, abstractmethod

from src.models.job import Job, JobStatus


class JobRepositoryPort(ABC):
    @abstractmethod
    async def create(self, job: Job) -> Job: ...

    @abstractmethod
    async def get(self, job_id: str) -> Job | None: ...

    @abstractmethod
    async def update_status(self, job_id: str, status: JobStatus) -> None: ...

    @abstractmethod
    async def save_result(self, job_id: str, result: dict) -> None: ...

    @abstractmethod
    async def mark_failed(self, job_id: str) -> None: ...

    @abstractmethod
    async def mark_cancelled(self, job_id: str) -> None: ...

    @abstractmethod
    async def start_artifact(self, job_id: str, node: str) -> None: ...

    @abstractmethod
    async def complete_artifact(self, job_id: str, node: str, status: str) -> None: ...

    @abstractmethod
    async def push_artifact_message(self, job_id: str, node: str, message: dict) -> None: ...

    @abstractmethod
    async def update_artifact_data(
        self, job_id: str, node: str, data: dict
    ) -> None: ...

    @abstractmethod
    async def get_pending(self) -> list[Job]: ...

    @abstractmethod
    async def list(
        self,
        page: int = 1,
        limit: int = 10,
        status: JobStatus | None = None,
        trace_id: str | None = None,
    ) -> tuple[list[Job], int]: ...
