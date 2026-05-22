import json
from unittest.mock import AsyncMock

import pytest

from src.domain.ports.container_run_port import ContainerRunPort
from src.utils.trivy import run_trivy


@pytest.mark.asyncio
async def test_run_trivy_returns_parsed_json():
    sample = {"bomFormat": "CycloneDX", "components": []}

    container = AsyncMock(spec=ContainerRunPort)
    container.run.return_value = (0, json.dumps(sample), "")

    result, stderr = await run_trivy(container, "/tmp/repo", "--format", "cyclonedx")

    assert result == sample
    assert stderr == ""
    container.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_trivy_raises_on_nonzero_exit():
    container = AsyncMock(spec=ContainerRunPort)
    container.run.return_value = (1, "", "permission denied")

    with pytest.raises(RuntimeError, match="Trivy exited 1"):
        await run_trivy(container, "/tmp/repo", "--format", "cyclonedx")


@pytest.mark.asyncio
async def test_run_trivy_raises_on_timeout():
    container = AsyncMock(spec=ContainerRunPort)
    container.run.return_value = (-1, "", "timed out after 300s")

    with pytest.raises(RuntimeError, match="Trivy exited -1"):
        await run_trivy(container, "/tmp/repo", "--format", "cyclonedx")
