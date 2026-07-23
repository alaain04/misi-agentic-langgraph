from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter


def _mock_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    proc = AsyncMock()
    proc.communicate.return_value = (stdout, stderr)
    proc.returncode = returncode
    return proc


@pytest.mark.asyncio
async def test_run_without_secret_env_unchanged_behavior():
    adapter = DockerContainerAdapter()
    with patch(
        "asyncio.create_subprocess_exec", return_value=_mock_proc()
    ) as mock_exec:
        await adapter.run(image="alpine/git", command="echo hi")

    assert mock_exec.call_args.kwargs["env"] is None
    assert "-e" not in mock_exec.call_args.args


@pytest.mark.asyncio
async def test_run_with_secret_env_never_puts_value_in_cmd_args():
    adapter = DockerContainerAdapter()
    with patch(
        "asyncio.create_subprocess_exec", return_value=_mock_proc()
    ) as mock_exec:
        await adapter.run(
            image="alpine/git",
            command="echo hi",
            secret_env={"GIT_TOKEN": "ghp_SECRETVALUE"},
        )

    call_args = mock_exec.call_args.args
    assert "ghp_SECRETVALUE" not in call_args
    assert "-e" in call_args
    assert "GIT_TOKEN" in call_args


@pytest.mark.asyncio
async def test_run_with_secret_env_passes_value_only_via_env_kwarg():
    adapter = DockerContainerAdapter()
    with patch(
        "asyncio.create_subprocess_exec", return_value=_mock_proc()
    ) as mock_exec:
        await adapter.run(
            image="alpine/git",
            command="echo hi",
            secret_env={"GIT_TOKEN": "ghp_SECRETVALUE"},
        )

    env = mock_exec.call_args.kwargs["env"]
    assert env["GIT_TOKEN"] == "ghp_SECRETVALUE"


@pytest.mark.asyncio
async def test_run_with_secret_env_never_logged(caplog):
    adapter = DockerContainerAdapter()
    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc()):
        with caplog.at_level("INFO"):
            await adapter.run(
                image="alpine/git",
                command="echo hi",
                secret_env={"GIT_TOKEN": "ghp_SECRETVALUE"},
            )

    assert "ghp_SECRETVALUE" not in caplog.text
