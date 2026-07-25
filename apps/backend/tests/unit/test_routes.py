from unittest.mock import AsyncMock, patch

import pytest

from src.api.routes import analyze
from src.api.schemas import AnalysisRequest

_REPO_URL = "https://github.com/example/repo"


@pytest.mark.asyncio
async def test_analyze_sets_used_pat_when_token_provided():
    dao = AsyncMock()
    request = AnalysisRequest(
        repo_url=_REPO_URL, concern="security", github_token="ghp_abc123"
    )

    with patch("src.api.routes.run_analysis", new=AsyncMock()):
        await analyze(request, dao=dao)

    created_job = dao.create.call_args.args[0]
    assert created_job.metadata.used_pat is True


@pytest.mark.asyncio
async def test_analyze_used_pat_false_without_token():
    dao = AsyncMock()
    request = AnalysisRequest(repo_url=_REPO_URL, concern="security")

    with patch("src.api.routes.run_analysis", new=AsyncMock()):
        await analyze(request, dao=dao)

    created_job = dao.create.call_args.args[0]
    assert created_job.metadata.used_pat is False


@pytest.mark.asyncio
async def test_analyze_token_never_persisted_in_job_doc():
    dao = AsyncMock()
    request = AnalysisRequest(
        repo_url=_REPO_URL, concern="security", github_token="ghp_SECRETVALUE"
    )

    with patch("src.api.routes.run_analysis", new=AsyncMock()):
        await analyze(request, dao=dao)

    created_job = dao.create.call_args.args[0]
    doc = created_job.to_doc()
    assert "ghp_SECRETVALUE" not in str(doc)


@pytest.mark.asyncio
async def test_analyze_passes_token_to_run_analysis():
    dao = AsyncMock()
    request = AnalysisRequest(
        repo_url=_REPO_URL, concern="security", github_token="ghp_abc123"
    )

    with patch("src.api.routes.run_analysis", new=AsyncMock()) as mock_run:
        await analyze(request, dao=dao)

    assert mock_run.call_args.kwargs["github_token"] == "ghp_abc123"


@pytest.mark.asyncio
async def test_analyze_passes_none_when_no_token():
    dao = AsyncMock()
    request = AnalysisRequest(repo_url=_REPO_URL, concern="security")

    with patch("src.api.routes.run_analysis", new=AsyncMock()) as mock_run:
        await analyze(request, dao=dao)

    assert mock_run.call_args.kwargs["github_token"] is None


@pytest.mark.asyncio
async def test_analyze_threads_remediate_flag():
    dao = AsyncMock()
    request = AnalysisRequest(
        repo_url=_REPO_URL, concern="security", remediate=True
    )

    with patch("src.api.routes.run_analysis", new=AsyncMock()) as mock_run:
        await analyze(request, dao=dao)

    assert mock_run.call_args.kwargs["remediate"] is True
