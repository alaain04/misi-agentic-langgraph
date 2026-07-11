from functools import lru_cache

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.services.job_dao import JobDAO
from src.db.result_dao import ResultDAO


@lru_cache(maxsize=1)
def get_job_repo() -> JobRepositoryPort:
    return JobDAO()


@lru_cache(maxsize=1)
def get_result_dao() -> ResultDAO:
    return ResultDAO()
