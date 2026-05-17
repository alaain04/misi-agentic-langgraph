from abc import ABC, abstractmethod


class JobRepositoryPort(ABC):
    @abstractmethod
    async def create(self, job_id: str, packages: list[str]) -> None: ...

    @abstractmethod
    async def record_success(self, job_id: str) -> None: ...

    @abstractmethod
    async def record_failure(self, job_id: str) -> None: ...

    @abstractmethod
    async def get_status(self, job_id: str) -> dict | None: ...

    @abstractmethod
    async def delete(self, job_id: str) -> None: ...
