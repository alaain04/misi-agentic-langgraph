"""Curated SPDX license knowledge base for the license conflict rule engine.

Approximates the term model in Liu et al., "Catch the Butterfly: Peeking
into the Terms and Conflicts among SPDX Licenses" (arXiv:2401.10636) for the
SPDX ids common in the npm ecosystem, rather than the paper's full 453-license
NLP extraction. Anything outside this table resolves to `unknown` in
`resolve()` rather than being guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Attitude = Literal["can", "cannot", "must", "not_required"]
Category = Literal[
    "public_domain",
    "permissive",
    "weak_copyleft",
    "strong_copyleft",
    "network_copyleft",
    "proprietary",
]


@dataclass(frozen=True)
class LicenseEntry:
    category: Category
    sublicense: Attitude
    commercial_use: Attitude
    include_notice: Attitude
    disclose_source: Attitude
    state_changes: Attitude
    same_license: Attitude


# Sentinel used when package.json has no "license" field or declares
# "UNLICENSED" — treated as proprietary/all-rights-reserved, the most
# restrictive stance (spec: this legitimately surfaces C1/C2 findings
# against most dependencies requiring attribution).
UNLICENSED_ID = "UNLICENSED"

LICENSES: dict[str, LicenseEntry] = {
    "MIT": LicenseEntry(
        category="permissive",
        sublicense="can",
        commercial_use="can",
        include_notice="must",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "ISC": LicenseEntry(
        category="permissive",
        sublicense="can",
        commercial_use="can",
        include_notice="must",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "Apache-2.0": LicenseEntry(
        category="permissive",
        sublicense="can",
        commercial_use="can",
        include_notice="must",
        disclose_source="not_required",
        state_changes="must",
        same_license="not_required",
    ),
    "BSD-2-Clause": LicenseEntry(
        category="permissive",
        sublicense="can",
        commercial_use="can",
        include_notice="must",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "BSD-3-Clause": LicenseEntry(
        category="permissive",
        sublicense="can",
        commercial_use="can",
        include_notice="must",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "0BSD": LicenseEntry(
        category="permissive",
        sublicense="can",
        commercial_use="can",
        include_notice="not_required",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "Unlicense": LicenseEntry(
        category="public_domain",
        sublicense="can",
        commercial_use="can",
        include_notice="not_required",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "CC0-1.0": LicenseEntry(
        category="public_domain",
        sublicense="can",
        commercial_use="can",
        include_notice="not_required",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "MPL-2.0": LicenseEntry(
        category="weak_copyleft",
        sublicense="can",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="not_required",
    ),
    "LGPL-2.1-only": LicenseEntry(
        category="weak_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="not_required",
    ),
    "LGPL-2.1-or-later": LicenseEntry(
        category="weak_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="not_required",
    ),
    "LGPL-3.0-only": LicenseEntry(
        category="weak_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="not_required",
    ),
    "LGPL-3.0-or-later": LicenseEntry(
        category="weak_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="not_required",
    ),
    "GPL-2.0-only": LicenseEntry(
        category="strong_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="must",
    ),
    "GPL-2.0-or-later": LicenseEntry(
        category="strong_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="must",
    ),
    "GPL-3.0-only": LicenseEntry(
        category="strong_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="must",
    ),
    "GPL-3.0-or-later": LicenseEntry(
        category="strong_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="must",
    ),
    "AGPL-3.0-only": LicenseEntry(
        category="network_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="must",
    ),
    "AGPL-3.0-or-later": LicenseEntry(
        category="network_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="must",
    ),
    UNLICENSED_ID: LicenseEntry(
        category="proprietary",
        sublicense="cannot",
        commercial_use="cannot",
        include_notice="not_required",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
}

_CATEGORY_RANK: dict[Category, int] = {
    "public_domain": 0,
    "permissive": 1,
    "weak_copyleft": 2,
    "strong_copyleft": 3,
    "network_copyleft": 4,
    "proprietary": 5,
}
_CAN_FIELDS = ("sublicense", "commercial_use")
_MUST_FIELDS = ("include_notice", "disclose_source", "state_changes", "same_license")


def _combine_and(a: LicenseEntry, b: LicenseEntry) -> LicenseEntry:
    """Merge two licenses under an SPDX "AND" expression: the recipient must
    satisfy both simultaneously, so any restriction or obligation on either
    side applies to the combination."""
    category = a.category if _CATEGORY_RANK[a.category] >= _CATEGORY_RANK[b.category] else b.category
    kwargs: dict[str, str] = {"category": category}
    for field in _CAN_FIELDS:
        kwargs[field] = (
            "cannot" if "cannot" in (getattr(a, field), getattr(b, field)) else "can"
        )
    for field in _MUST_FIELDS:
        kwargs[field] = (
            "must" if "must" in (getattr(a, field), getattr(b, field)) else "not_required"
        )
    return LicenseEntry(**kwargs)  # type: ignore[arg-type]


def resolve(expression: str | None) -> tuple[str, LicenseEntry] | None:
    """Normalize a raw SPDX license expression to a curated (id, entry) pair.

    Supports exact ids and single-level "A OR B" / "A AND B" expressions.
    Anything else — custom text, `SEE LICENSE IN <file>`, nested/parenthesized
    expressions, or an id outside the curated table — returns None. The
    caller must record this as a manual-review finding, never guess.
    """
    expr = (expression or "").strip()
    if not expr or "(" in expr or ")" in expr:
        return None
    if expr in LICENSES:
        return expr, LICENSES[expr]
    if " OR " in expr:
        left, right = (side.strip() for side in expr.split(" OR ", 1))
        for side in (left, right):
            if side in LICENSES:
                return side, LICENSES[side]
        return None
    if " AND " in expr:
        left, right = (side.strip() for side in expr.split(" AND ", 1))
        if left in LICENSES and right in LICENSES:
            return f"{left} AND {right}", _combine_and(LICENSES[left], LICENSES[right])
        return None
    return None
