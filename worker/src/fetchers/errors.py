class RateLimitError(Exception):
    """API responded 429. delay is seconds to wait before retrying."""

    def __init__(self, delay: float) -> None:
        super().__init__(f"rate limited, retry after {delay}s")
        self.delay = delay


class TransientFetchError(Exception):
    """Temporary failure (5xx, network error). Safe to retry."""


class PermanentFetchError(Exception):
    """Unrecoverable failure (404, parse error). Do not retry."""
