from functools import lru_cache

from src.db.input_cache import InputCacheDAO
from src.db.result_dao import ResultDAO
from src.domain.ports.job_repository_port import JobRepositoryPort
from src.services.job_dao import JobDAO


@lru_cache(maxsize=1)
def get_job_repo() -> JobRepositoryPort:
    return JobDAO()


@lru_cache(maxsize=1)
def get_result_dao() -> ResultDAO:
    return ResultDAO()


@lru_cache(maxsize=1)
def get_input_cache() -> InputCacheDAO:
    return InputCacheDAO()
