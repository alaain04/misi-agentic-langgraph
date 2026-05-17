import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.models.errors import PermanentFetchError, RateLimitError, TransientFetchError
from domain.ports.entity_cache_port import EntityCachePort
from domain.ports.fetcher_port import FetcherPort
from domain.ports.job_repository_port import JobRepositoryPort
from domain.ports.messaging_port import MessagingPort
from services.application_services.consumer_service import ConsumerService


def _make_settings():
    s = MagicMock()
    s.NATS_MAX_DELIVER = 5
    s.NATS_TRANSIENT_BACKOFF_BASE = 5.0
    s.NATS_TRANSIENT_BACKOFF_CAP = 300.0
    s.WORKER_CONCURRENCY = 1
    s.NATS_STREAM_NAME = "ENTITY_FETCH"
    s.NATS_SUBJECT_PREFIX = "entity.fetch"
    return s


def _make_msg(entity_type: str = "npm", num_delivered: int = 1) -> MagicMock:
    msg = MagicMock()
    msg.data = json.dumps(
        {"job_id": "j1", "entity_type": entity_type, "name": "react"}
    ).encode()
    msg.metadata = MagicMock()
    msg.metadata.num_delivered = num_delivered
    return msg


def _make_service():
    mock_messaging = MagicMock(spec=MessagingPort)
    mock_messaging.ack = AsyncMock()
    mock_messaging.nak = AsyncMock()
    mock_messaging.term = AsyncMock()

    mock_fetcher = MagicMock(spec=FetcherPort)
    mock_fetcher.fetch = AsyncMock(return_value={"data": {}})
    mock_fetcher.collection = "npm_package_cache"

    mock_entity_cache = MagicMock(spec=EntityCachePort)
    mock_entity_cache.save = AsyncMock()

    mock_job_repo = MagicMock(spec=JobRepositoryPort)
    mock_job_repo.record_success = AsyncMock()
    mock_job_repo.record_failure = AsyncMock()

    service = ConsumerService(
        messaging=mock_messaging,
        fetcher_registry={"npm": mock_fetcher},
        entity_cache=mock_entity_cache,
        job_repo=mock_job_repo,
        settings=_make_settings(),
    )
    return service, mock_messaging, mock_fetcher, mock_entity_cache, mock_job_repo


async def test_process_success_acks_and_records_success():
    service, messaging, fetcher, entity_cache, job_repo = _make_service()
    await service._process(_make_msg(), AsyncMock())
    messaging.ack.assert_called_once()
    job_repo.record_success.assert_called_once_with("j1")
    messaging.nak.assert_not_called()
    messaging.term.assert_not_called()


async def test_process_rate_limit_naks_with_delay():
    service, messaging, fetcher, _, job_repo = _make_service()
    fetcher.fetch = AsyncMock(side_effect=RateLimitError(45.0))
    await service._process(_make_msg(), AsyncMock())
    messaging.nak.assert_called_once()
    call_args = messaging.nak.call_args
    assert call_args[1]["delay"] == 45.0
    job_repo.record_failure.assert_not_called()


async def test_process_permanent_error_terms_and_records_failure():
    service, messaging, fetcher, _, job_repo = _make_service()
    fetcher.fetch = AsyncMock(side_effect=PermanentFetchError("404"))
    await service._process(_make_msg(), AsyncMock())
    messaging.term.assert_called_once()
    job_repo.record_failure.assert_called_once_with("j1")


async def test_process_transient_naks_when_retries_remain():
    service, messaging, fetcher, _, job_repo = _make_service()
    fetcher.fetch = AsyncMock(side_effect=TransientFetchError("timeout"))
    await service._process(_make_msg(num_delivered=2), AsyncMock())
    messaging.nak.assert_called_once()
    nak_kwargs = messaging.nak.call_args[1]
    assert nak_kwargs["delay"] > 0
    job_repo.record_failure.assert_not_called()


async def test_process_transient_terms_when_max_deliver_exhausted():
    service, messaging, fetcher, _, job_repo = _make_service()
    fetcher.fetch = AsyncMock(side_effect=TransientFetchError("timeout"))
    await service._process(_make_msg(num_delivered=5), AsyncMock())
    messaging.term.assert_called_once()
    job_repo.record_failure.assert_called_once_with("j1")


async def test_process_unexpected_error_terms_and_records_failure():
    service, messaging, fetcher, _, job_repo = _make_service()
    fetcher.fetch = AsyncMock(side_effect=RuntimeError("unexpected"))
    await service._process(_make_msg(), AsyncMock())
    messaging.term.assert_called_once()
    job_repo.record_failure.assert_called_once_with("j1")
