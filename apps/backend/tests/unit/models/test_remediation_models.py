from src.models.remediation import (
    CodeChange,
    Remediation,
    RemediationResult,
    RemediationTarget,
    VerificationResult,
)


def test_remediation_bump_defaults_are_tier0_1():
    r = Remediation(addresses=["lodash"], target_dep="lodash")
    assert r.strategy == "bump"
    assert r.status == "skipped"
    assert r.replacement_dep is None
    assert r.migration_plan == ""
    assert r.code_changes == []
    assert r.verification.installed is False
    assert r.verification.built is None
    assert r.attempts == 0
    assert r.patch == ""


def test_verification_partial_is_representable():
    v = VerificationResult(
        installed=True, built=None, tested=None, finding_resolved=True
    )
    assert v.built is None and v.tested is None and v.finding_resolved is True


def test_remediation_result_round_trip():
    res = RemediationResult(
        job_id="j1",
        remediations=[
            Remediation(
                addresses=["minimist"],
                target_dep="mkdirp",
                strategy="bump",
                from_range="^0.5.1",
                to_range="^0.5.5",
                status="fixed",
            )
        ],
        branch="remediation/j1",
        pr_url="https://gh/pr/1",
        consent=True,
    )
    doc = res.model_dump()
    assert RemediationResult(**doc).remediations[0].target_dep == "mkdirp"


def test_remediation_target_carries_addresses():
    t = RemediationTarget(target_dep="mkdirp", addresses=["minimist", "mkdirp"])
    assert t.addresses == ["minimist", "mkdirp"]


def test_code_change_shape():
    c = CodeChange(file="src/a.js", rationale="api moved")
    assert c.file == "src/a.js"
