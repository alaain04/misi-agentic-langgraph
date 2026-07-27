"""
Shared fixtures for blackbox subgraph integration tests.

Requires Docker (for testcontainers MongoDB). Run with:
    uv run pytest tests/subgraphs/ -v

If Docker is not running, all tests are skipped automatically.
Start Docker/Colima first: colima start
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo import AsyncMongoClient
from testcontainers.mongodb import MongoDbContainer

from src.db.result_dao import ResultDAO

# Dummy env vars so pydantic-settings doesn't error on import
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")

# Disable testcontainers Ryuk reaper — avoids port-8080 conflicts on repeated runs
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
        return True
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        return False


_requires_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker is not running — start it first (e.g. colima start)",
)


@pytest.fixture(scope="session")
def mongo_container():
    """Start a MongoDB container once for the entire test session."""
    if not _docker_available():
        pytest.skip("Docker is not running — start it first (e.g. colima start)")
    with MongoDbContainer("mongo:7") as container:
        yield container


@pytest.fixture(scope="session")
def mongo_uri(mongo_container):
    return mongo_container.get_connection_url()


@pytest.fixture
async def result_dao(mongo_uri):
    """
    Function-scoped DAO backed by a real MongoDB testcontainer.
    Each test gets clean collections; they're dropped on teardown.
    """
    client = AsyncMongoClient(mongo_uri)
    db = client["test_subgraphs"]

    # Bypass ResultDAO.__init__ which reads from settings
    dao = object.__new__(ResultDAO)
    dao._prep = db["prep_results"]
    dao._bundles = db["evidence_bundles"]
    dao._analysis = db["analysis_results"]
    dao._report = db["report_results"]
    dao._remediation = db["remediation_results"]

    yield dao

    await db["prep_results"].drop()
    await db["evidence_bundles"].drop()
    await db["analysis_results"].drop()
    await db["report_results"].drop()
    await db["remediation_results"].drop()
    await client.aclose()


@pytest.fixture
async def subgraph_config(result_dao):
    """
    RunnableConfig dict suitable for any of the three subgraphs.
    The container mock returns a success exit code so clone_repo
    doesn't need Docker.
    """
    container_mock = MagicMock()
    container_mock.run = AsyncMock(return_value=(0, "", ""))

    return {
        "configurable": {
            "result_dao": result_dao,
            "container": container_mock,
            "docker_tool": MagicMock(),
            "job_repo": AsyncMock(),
        }
    }
