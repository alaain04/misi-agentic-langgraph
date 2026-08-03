from src.models.remediation import (
    CodeChange,
    MigrationPlan,
    MigrationTask,
    Remediation,
    ReleaseDigest,
    RemediationOutcome,
    RemediationResult,
    RemediationTarget,
    TargetInvestigation,
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


def test_remediation_carries_required_by_and_pr_fields():
    r = Remediation(
        addresses=[],
        target_dep="eslint-plugin-react",
        required_by=["eslint"],
    )
    assert r.required_by == ["eslint"]
    assert r.branch is None
    assert r.pr_url is None


def test_remediation_outcome_defaults_are_bump_only():
    outcome = RemediationOutcome()
    assert outcome.strategy == "bump"
    assert outcome.requires == []
    assert outcome.code_diff == ""
    assert outcome.status == "skipped"


def test_remediation_outcome_replace_fields():
    outcome = RemediationOutcome(
        strategy="replace",
        replacement_dep="fast-glob",
        replacement_range="^3.0.0",
        requires=["some-plugin"],
    )
    assert outcome.replacement_dep == "fast-glob"
    assert outcome.requires == ["some-plugin"]


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
                branch="remediation/j1-mkdirp",
                pr_url="https://gh/pr/1",
            )
        ],
        consent=True,
    )
    doc = res.model_dump()
    restored = RemediationResult(**doc)
    assert restored.remediations[0].target_dep == "mkdirp"
    assert restored.remediations[0].pr_url == "https://gh/pr/1"


def test_remediation_target_carries_addresses():
    t = RemediationTarget(target_dep="mkdirp", addresses=["minimist", "mkdirp"])
    assert t.addresses == ["minimist", "mkdirp"]


def test_code_change_shape():
    c = CodeChange(file="src/a.js", rationale="api moved")
    assert c.file == "src/a.js"


def test_release_digest_defaults():
    d = ReleaseDigest(from_version="1.0.0", to_version="2.0.0", migration_needed=True)
    assert d.migration_guide == ""
    assert d.breaking_changes == []


def test_target_investigation_round_trip():
    inv = TargetInvestigation(
        target_dep="lodash",
        dependents=["a"],
        call_sites=["src/x.ts"],
        release=ReleaseDigest(from_version=None, to_version=None, migration_needed=False),
    )
    assert TargetInvestigation(**inv.model_dump()).call_sites == ["src/x.ts"]


def test_migration_plan_defaults_and_task():
    plan = MigrationPlan(
        target_dep="lodash",
        tier_hint="r2",
        tasks=[MigrationTask(kind="bump", rationale="patch", to_range="^4.17.21")],
    )
    assert plan.requires == []
    assert plan.migration_guide == ""
    assert plan.tasks[0].kind == "bump"


def test_remediation_target_carries_tier():
    t = RemediationTarget(target_dep="lodash", addresses=["lodash"], tier="r1")
    assert t.tier == "r1"
    assert RemediationTarget(target_dep="x", addresses=[]).tier is None


def test_remediation_embeds_plan():
    plan = MigrationPlan(target_dep="lodash", tier_hint="r1", tasks=[])
    r = Remediation(addresses=[], target_dep="lodash", plan=plan)
    assert r.plan.target_dep == "lodash"
    assert Remediation(addresses=[], target_dep="x").plan is None
