from __future__ import annotations

from typing import Protocol, TypeVar

from src.utils.config import settings

SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


class _HasSeverity(Protocol):
    severity: str


T = TypeVar("T", bound=_HasSeverity)


def filter_by_min_severity(items: list[T]) -> list[T]:  # noqa: UP047
    min_severity = settings.risk_min_severity
    if min_severity == "any":
        return items
    threshold = SEVERITY_ORDER.get(min_severity, 0)
    return [item for item in items if SEVERITY_ORDER.get(item.severity, 0) >= threshold]
