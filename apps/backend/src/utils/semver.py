"""Minimal semver parsing shared by version-comparison logic across
subgraphs (Trivy vuln finding grouping, remediation release classification)."""

from __future__ import annotations

import re

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_RANGE_PREFIX_RE = re.compile(r"^[v=^~><\s]+")


def parse_semver(version: str) -> tuple[int, int, int] | None:
    """(major, minor, patch), or None if not a plain semver string (e.g. a
    compound Trivy FixedVersion like "3.2.19, 4.1.9", or non-numeric text)."""
    match = _SEMVER_RE.match(version.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def is_semver_major_bump(installed: str | None, fixed: str | None) -> bool | None:
    """True/False only when both sides parse as a single semver; None
    otherwise (no fix available, or either string isn't plain semver)."""
    if not installed or not fixed:
        return None
    installed_parsed = parse_semver(installed)
    fixed_parsed = parse_semver(fixed)
    if installed_parsed is None or fixed_parsed is None:
        return None
    return installed_parsed[0] != fixed_parsed[0]


def range_floor(range_str: str | None) -> tuple[int, int, int] | None:
    """The lowest version a simple npm range accepts ("^4.17.11" ->
    (4, 17, 11)), or None when there is no single parseable base (a compound
    range, "*", "latest", a git/file URL)."""
    if not range_str:
        return None
    return parse_semver(_RANGE_PREFIX_RE.sub("", range_str.strip()))


def is_noop_range_change(current: str | None, new: str | None) -> bool:
    """True when `new` is provably not an upgrade over `current`: the two
    npm ranges are the same string and pin a concrete semver ("0.7.0" ->
    "0.7.0", "^1.2.3" -> "^1.2.3").

    Deliberately conservative -- it must never call a real upgrade a no-op,
    so anything else is False, including a widening change like "1.2.3" ->
    "^1.2.3" (which CAN resolve higher, and is only a no-op when nothing
    newer was ever published -- a fact this module cannot see without the
    registry's latest_version).
    """
    if not current or not new:
        return False
    current, new = current.strip(), new.strip()
    if current != new:
        return False
    return parse_semver(_RANGE_PREFIX_RE.sub("", current)) is not None


def max_semver(versions: list[str]) -> str | None:
    """The highest of the given version strings that parse as semver, or
    None if none do. Non-parseable entries (compound ranges, "unknown",
    etc.) are ignored rather than breaking the comparison."""
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for v in versions:
        parsed = parse_semver(v) if v else None
        if parsed is not None:
            candidates.append((parsed, v))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]
