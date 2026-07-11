"""Typed configurable dict for all pipeline infrastructure ports."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from typing_extensions import TypedDict

from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.job_repository_port import JobRepositoryPort


class PipelineConfigurable(TypedDict):
    job_repo: JobRepositoryPort
    container: ContainerRunPort
    docker_tool: BaseTool


def get_services(config: RunnableConfig) -> PipelineConfigurable:
    return config["configurable"]
