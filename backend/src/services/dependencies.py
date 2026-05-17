from functools import lru_cache

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.services.job_dao import JobDAO


@lru_cache(maxsize=1)
def get_job_repo() -> JobRepositoryPort:
    return JobDAO()
