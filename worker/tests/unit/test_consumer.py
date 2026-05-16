# tests/unit/test_consumer.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.fetchers.errors import PermanentFetchError, RateLimitError, TransientFetchError


def _make_msg(num_delivered: int = 1):
    msg = MagicMock()
    msg.data = b'{"job_id":"j1","entity_type":"npm","name":"react"}'
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()
    msg.metadata = MagicMock()
    msg.metadata.num_delivered = num_delivered
    return msg


@pytest.mark.asyncio
async def test_process_success_acks_and_records_success():
    msg = _make_msg()
    mock_entry = MagicMock()
    mock_entry.fetch_fn = AsyncMock(return_value={"registry_data": {}})
    mock_entry.collection = "npm_package_cache"
    mock_entry.rate_group = "npm"

    with (
        patch("src.consumer.fetchers.get", return_value=mock_entry),
        patch("src.consumer._save", new_callable=AsyncMock),
        patch("src.consumer.jobs.record_success", new_callable=AsyncMock) as mock_success,
    ):
        from src.consumer import _process
        limiter = MagicMock()
        await _process(msg, AsyncMock(), limiter)

    msg.ack.assert_called_once()
    mock_success.assert_called_once_with("j1")
    msg.nak.assert_not_called()
    msg.term.assert_not_called()


@pytest.mark.asyncio
async def test_process_rate_limit_naks_with_delay():
    msg = _make_msg()
    mock_entry = MagicMock()
    mock_entry.fetch_fn = AsyncMock(side_effect=RateLimitError(45.0))
    mock_entry.collection = "npm_package_cache"
    mock_entry.rate_group = "npm"

    with (
        patch("src.consumer.fetchers.get", return_value=mock_entry),
        patch("src.consumer.jobs.record_failure", new_callable=AsyncMock) as mock_fail,
    ):
        from src.consumer import _process
        limiter = MagicMock()
        await _process(msg, AsyncMock(), limiter)

    msg.nak.assert_called_once_with(delay=45.0)
    msg.ack.assert_not_called()
    mock_fail.assert_not_called()


@pytest.mark.asyncio
async def test_process_transient_error_naks_with_backoff():
    msg = _make_msg(num_delivered=2)
    mock_entry = MagicMock()
    mock_entry.fetch_fn = AsyncMock(side_effect=TransientFetchError("timeout"))
    mock_entry.collection = "npm_package_cache"
    mock_entry.rate_group = "npm"

    with (
        patch("src.consumer.fetchers.get", return_value=mock_entry),
        patch("src.consumer.jobs.record_failure", new_callable=AsyncMock) as mock_fail,
        patch("src.consumer.settings") as mock_settings,
    ):
        mock_settings.nats_transient_backoff_base = 5.0
        mock_settings.nats_transient_backoff_cap = 300.0
        mock_settings.nats_max_deliver = 5
        from src.consumer import _process
        limiter = MagicMock()
        await _process(msg, AsyncMock(), limiter)

    msg.nak.assert_called_once()
    nak_delay = msg.nak.call_args.kwargs.get("delay") or msg.nak.call_args[1].get("delay")
    assert nak_delay > 0
    mock_fail.assert_not_called()


@pytest.mark.asyncio
async def test_process_permanent_error_terms_and_records_failure():
    msg = _make_msg()
    mock_entry = MagicMock()
    mock_entry.fetch_fn = AsyncMock(side_effect=PermanentFetchError("404"))
    mock_entry.collection = "npm_package_cache"
    mock_entry.rate_group = "npm"

    with (
        patch("src.consumer.fetchers.get", return_value=mock_entry),
        patch("src.consumer.jobs.record_failure", new_callable=AsyncMock) as mock_fail,
    ):
        from src.consumer import _process
        limiter = MagicMock()
        await _process(msg, AsyncMock(), limiter)

    msg.term.assert_called_once()
    msg.ack.assert_not_called()
    mock_fail.assert_called_once_with("j1")


@pytest.mark.asyncio
async def test_process_terms_when_max_deliver_exhausted():
    msg = _make_msg(num_delivered=5)  # equals nats_max_deliver
    mock_entry = MagicMock()
    mock_entry.fetch_fn = AsyncMock(side_effect=TransientFetchError("timeout"))
    mock_entry.collection = "npm_package_cache"
    mock_entry.rate_group = "npm"

    with (
        patch("src.consumer.fetchers.get", return_value=mock_entry),
        patch("src.consumer.jobs.record_failure", new_callable=AsyncMock) as mock_fail,
        patch("src.consumer.settings") as mock_settings,
    ):
        mock_settings.nats_transient_backoff_base = 5.0
        mock_settings.nats_transient_backoff_cap = 300.0
        mock_settings.nats_max_deliver = 5
        from src.consumer import _process
        limiter = MagicMock()
        await _process(msg, AsyncMock(), limiter)

    msg.term.assert_called_once()
    mock_fail.assert_called_once_with("j1")
