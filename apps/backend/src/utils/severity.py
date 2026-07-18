from __future__ import annotations

from typing import Protocol

SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


class _HasSeverity(Protocol):
    severity: str


def filter_by_min_severity[T: _HasSeverity](
    items: list[T], min_severity: str
) -> list[T]:
    if min_severity == "any":
        return items
    threshold = SEVERITY_ORDER.get(min_severity, 0)
    return [item for item in items if SEVERITY_ORDER.get(item.severity, 0) >= threshold]
