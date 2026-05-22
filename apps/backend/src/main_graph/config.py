"""Typed configurable dict for all pipeline infrastructure ports."""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from typing_extensions import TypedDict

from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.domain.ports.job_repository_port import JobRepositoryPort
from src.domain.ports.vector_store_port import VectorStorePort
from src.main_graph.subgraphs.ingestion_subgraphs.repo.dao import RepoCacheDAO
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.dao import RuntimeCacheDAO


class PipelineConfigurable(TypedDict):
    job_repo: JobRepositoryPort
    vector_store: VectorStorePort
    container: ContainerRunPort
    docker_tool: BaseTool
    ingestion_daos: dict[str, IngestionResultPort]
    sbom_dao: IngestionResultPort
    repo_cache_dao: RepoCacheDAO
    runtime_cache_dao: RuntimeCacheDAO


def get_services(config: RunnableConfig) -> PipelineConfigurable:
    return config["configurable"]
