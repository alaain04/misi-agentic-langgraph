"""Typed configurable dict for all pipeline infrastructure ports."""

from __future__ import annotations

from typing import NotRequired, cast

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from typing_extensions import TypedDict

from src.db.input_cache import InputCacheDAO
from src.db.result_dao import ResultDAO
from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.job_repository_port import JobRepositoryPort


class PipelineConfigurable(TypedDict):
    job_repo: JobRepositoryPort
    container: ContainerRunPort
    docker_tool: BaseTool
    result_dao: ResultDAO
    # Optional: absent in lightweight contexts (run_subgraph script, tests).
    # Consumers guard with svc.get("input_cache").
    input_cache: NotRequired[InputCacheDAO]
    # Optional: per-request PAT for private-repo clone (Workstream D1).
    # Never persisted — threaded from the /analyze request body only, and
    # absent from graph state entirely. Consumers guard with
    # svc.get("github_token").
    github_token: NotRequired[str]


def get_services(config: RunnableConfig) -> PipelineConfigurable:
    return cast(PipelineConfigurable, config["configurable"])
