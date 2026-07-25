import pytest

from src.main_graph.subgraphs.remediation.orchestrator import run_remediation
from src.models.remediation import (
    RemediationDecision,
    RemediationTarget,
    VerificationResult,
)

GREEN = VerificationResult(
    installed=True, built=True, tested=True, finding_resolved=True
)
REDTEST = VerificationResult(
    installed=True, built=True, tested=False, finding_resolved=True
)


def _target(dep, addresses=None):
    return RemediationTarget(
        target_dep=dep, addresses=addresses or [dep], current_range="^1.0.0"
    )


def _scripted_decider(actions):
    seq = iter(actions)

    async def decide(_prompt):
        return next(seq)

    return decide


def _apply_ok(work_dir, dep, rng):
    return True


@pytest.mark.asyncio
async def test_single_bump_verifies_and_fixes():
    verified = []

    async def verify(targeted):
        verified.append(targeted)
        return GREEN

    async def diff():
        return "PATCH"

    decide = _scripted_decider(
        [
            RemediationDecision(
                action="bump", target_dep="lodash", to_range="^4.17.21"
            ),
            RemediationDecision(action="finalize"),
        ]
    )
    out = await run_remediation(
        [_target("lodash")], "/w", {}, _apply_ok, verify, diff, decide=decide
    )
    r = {x.target_dep: x for x in out}["lodash"]
    assert r.status == "fixed" and r.to_range == "^4.17.21" and r.patch == "PATCH"
    assert r.attempts == 1


@pytest.mark.asyncio
async def test_failed_verification_then_adjust_succeeds():
    results = iter([REDTEST, GREEN])

    async def verify(targeted):
        return next(results)

    async def diff():
        return "PATCH"

    decide = _scripted_decider(
        [
            RemediationDecision(
                action="bump", target_dep="lodash", to_range="^4.17.20"
            ),
            RemediationDecision(
                action="bump", target_dep="lodash", to_range="^4.17.21"
            ),
            RemediationDecision(action="finalize"),
        ]
    )
    out = await run_remediation(
        [_target("lodash")], "/w", {}, _apply_ok, verify, diff, decide=decide
    )
    r = out[0]
    assert r.status == "fixed" and r.to_range == "^4.17.21" and r.attempts == 2


@pytest.mark.asyncio
async def test_skip_records_tier2_breadcrumb():
    async def verify(targeted):
        return GREEN

    async def diff():
        return ""

    decide = _scripted_decider(
        [
            RemediationDecision(
                action="skip", target_dep="chalk", skip_reason="needs major (Tier 2)"
            ),
            RemediationDecision(action="finalize"),
        ]
    )
    out = await run_remediation(
        [_target("chalk")], "/w", {}, _apply_ok, verify, diff, decide=decide
    )
    r = out[0]
    assert r.status == "skipped" and r.strategy == "bump_with_codemod"


@pytest.mark.asyncio
async def test_undeclared_dep_marks_failed():
    async def verify(targeted):
        return GREEN

    async def diff():
        return ""

    def apply_false(w, d, r):
        return False

    decide = _scripted_decider(
        [
            RemediationDecision(action="bump", target_dep="ghost", to_range="^9"),
            RemediationDecision(action="finalize"),
        ]
    )
    out = await run_remediation(
        [_target("ghost")], "/w", {}, apply_false, verify, diff, decide=decide
    )
    assert out[0].status == "failed"


@pytest.mark.asyncio
async def test_bounded_iterations_marks_unresolved_failed():
    async def verify(targeted):
        return REDTEST  # never green

    async def diff():
        return ""

    async def always_bump(_prompt):
        return RemediationDecision(
            action="bump", target_dep="lodash", to_range="^4.17.21"
        )

    out = await run_remediation(
        [_target("lodash")],
        "/w",
        {},
        _apply_ok,
        verify,
        diff,
        decide=always_bump,
        max_iterations=3,
    )
    assert out[0].status == "failed" and out[0].attempts >= 1


@pytest.mark.asyncio
async def test_cross_bump_regression_reverts_earlier_green_status():
    """B's bump breaks the joint copy after A alone had verified green.

    The invariant is that whatever ships verifies TOGETHER — an earlier
    per-target green result must not survive a later regression. Both must
    end up "failed" since the FINAL joint verification (a+b) never went
    green, even though a-alone did at one point.
    """

    async def verify(targeted):
        return GREEN if "b" not in targeted else REDTEST

    async def diff():
        return "PATCH"

    decide = _scripted_decider(
        [
            RemediationDecision(action="bump", target_dep="a", to_range="^2.0.0"),
            RemediationDecision(action="bump", target_dep="b", to_range="^3.0.0"),
            RemediationDecision(action="finalize"),
        ]
    )
    targets = [_target("a"), _target("b")]
    out = await run_remediation(
        targets, "/w", {}, _apply_ok, verify, diff, decide=decide
    )
    by_dep = {r.target_dep: r for r in out}
    assert by_dep["a"].status == "failed"
    assert by_dep["b"].status == "failed"


@pytest.mark.asyncio
async def test_skip_reverts_previously_applied_bump():
    """A dep bumped then later skipped must have its package.json edit
    reverted immediately, before subsequent verify() calls run over a
    working copy still carrying the stale, discarded edit."""
    calls = []

    def spy_apply_bump(work_dir, dep, rng):
        calls.append((work_dir, dep, rng))
        return True

    async def verify(targeted):
        return GREEN

    async def diff():
        return "PATCH"

    decide = _scripted_decider(
        [
            RemediationDecision(action="bump", target_dep="b", to_range="^2.0.0"),
            RemediationDecision(
                action="skip", target_dep="b", skip_reason="needs major (Tier 2)"
            ),
            RemediationDecision(action="finalize"),
        ]
    )
    out = await run_remediation(
        [_target("b")], "/w", {}, spy_apply_bump, verify, diff, decide=decide
    )
    r = out[0]
    assert r.status == "skipped"
    assert calls == [
        ("/w", "b", "^2.0.0"),
        ("/w", "b", "^1.0.0"),
    ]


@pytest.mark.asyncio
async def test_unresolved_failed_dep_reverted_before_patch():
    """A dep that never resolves (bounded-iterations exhaustion, ending
    "failed") must have its bump reverted before the final diff() runs,
    so its leftover edit doesn't leak into another dep's "fixed" patch."""
    calls = []

    def spy_apply_bump(work_dir, dep, rng):
        calls.append((work_dir, dep, rng))
        return True

    async def verify(targeted):
        return REDTEST  # never green

    async def diff():
        return "PATCH"

    async def always_bump(_prompt):
        return RemediationDecision(
            action="bump", target_dep="lodash", to_range="^4.17.21"
        )

    out = await run_remediation(
        [_target("lodash")],
        "/w",
        {},
        spy_apply_bump,
        verify,
        diff,
        decide=always_bump,
        max_iterations=3,
    )
    r = out[0]
    assert r.status == "failed"
    lodash_calls = [c for c in calls if c[1] == "lodash"]
    assert ("/w", "lodash", "^4.17.21") in lodash_calls
    assert ("/w", "lodash", "^1.0.0") in lodash_calls
    assert len(lodash_calls) >= 2
