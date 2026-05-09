import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.trivy import run_trivy


@pytest.mark.asyncio
async def test_run_trivy_returns_parsed_json():
    sample = {"bomFormat": "CycloneDX", "components": []}
    encoded = json.dumps(sample).encode()

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(encoded, b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result, stderr = await run_trivy("/tmp/repo", "--format", "cyclonedx")

    assert result == sample
    assert stderr == ""


@pytest.mark.asyncio
async def test_run_trivy_raises_on_nonzero_exit():
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"permission denied"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(RuntimeError, match="Trivy exited 1"):
            await run_trivy("/tmp/repo", "--format", "cyclonedx")


@pytest.mark.asyncio
async def test_run_trivy_raises_on_timeout():
    import asyncio as _asyncio

    mock_proc = MagicMock()
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()
    mock_proc.communicate = AsyncMock(side_effect=_asyncio.TimeoutError())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", side_effect=TimeoutError()):
        with pytest.raises(RuntimeError, match="timed out"):
            await run_trivy("/tmp/repo", "--format", "cyclonedx")
