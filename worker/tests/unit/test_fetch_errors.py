from src.fetchers.errors import RateLimitError, TransientFetchError, PermanentFetchError


def test_rate_limit_error_stores_delay():
    err = RateLimitError(delay=30.0)
    assert err.delay == 30.0
    assert isinstance(err, Exception)


def test_transient_fetch_error_is_exception():
    err = TransientFetchError("network timeout")
    assert str(err) == "network timeout"


def test_permanent_fetch_error_is_exception():
    err = PermanentFetchError("404 not found")
    assert str(err) == "404 not found"
