from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.main_graph.subgraphs.remediation.nodes.pr_and_persist import (
    _pr_findings_table,
    _pr_title_and_body,
    _pr_verification_summary,
    pr_and_persist_node,
)
from src.models.remediation import FindingSummary, Remediation, VerificationResult
from src.models.results import PrepResult


def _prep(**overrides):
    defaults = dict(
        id="prep-1",
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={},
        manifest_files=["package.json"],
        package_manager="npm",
        docker_image="node:lts-alpine",
        dependency_graph={"direct": {"lodash": "^4.17.11"}, "packages": {}},
    )
    defaults.update(overrides)
    return PrepResult(**defaults)


@pytest.mark.asyncio
async def test_pr_and_persist_node_skips_pr_when_consent_false():
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    dao.save_remediation = AsyncMock(return_value="rid-1")
    git_pr = AsyncMock()
    config = {
        "configurable": {
            "result_dao": dao,
            "container": MagicMock(),
            "remediate": False,
            "git_pr": git_pr,
        }
    }

    # A verified_workdirs entry is present on purpose: the node must refuse to
    # ship on its own (`consent` check), not merely rely on the gate never
    # having populated the channel.
    mkdtemp_root = tempfile.mkdtemp(prefix="test-remediation-")
    work_dir = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work_dir)

    state = {
        "job_id": "job-1",
        "prep_result_id": "prep-1",
        "remediations": {
            "lodash": {
                "id": "r1",
                "addresses": ["lodash"],
                "target_dep": "lodash",
                "strategy": "bump",
                "to_range": "^4.17.21",
                "status": "fixed",
            }
        },
        "requires_edges": {},
        "verified_workdirs": {"lodash": work_dir},
    }
    result = await pr_and_persist_node(state, config)

    git_pr.open_pr.assert_not_called()
    assert result == {"remediation_result_id": "rid-1"}
    assert not os.path.exists(mkdtemp_root)


@pytest.mark.asyncio
async def test_pr_and_persist_node_skips_group_not_all_fixed():
    """Part 1's cleanup deletes a superseded/invalidated kept directory but
    cannot remove the key from `verified_workdirs` (_merge_replace only
    overwrites), so a dangling entry can survive into this node. Shipping it
    would open a PR for a group the gate did NOT settle as fixed, using stale
    verification data. Any member not `fixed` blocks the whole group, and the
    (stale) work dir is cleaned up."""
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep(repo_path="/original/repo"))
    dao.save_remediation = AsyncMock(return_value="rid-1")
    git_pr = AsyncMock()
    git_pr.open_pr = AsyncMock(return_value="https://gh/pr/1")
    config = {
        "configurable": {
            "result_dao": dao,
            "container": MagicMock(),
            "remediate": True,
            "git_pr": git_pr,
        }
    }

    mkdtemp_root = tempfile.mkdtemp(prefix="test-remediation-")
    work_dir = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work_dir)

    verification = {"installed": True, "finding_resolved": True}
    state = {
        "job_id": "job-1",
        "prep_result_id": "prep-1",
        "remediations": {
            "eslint": {
                "id": "r1",
                "addresses": ["eslint"],
                "target_dep": "eslint",
                "strategy": "bump",
                "to_range": "^9.0.0",
                "status": "fixed",
                "verification": verification,
            },
            "eslint-plugin-react": {
                "id": "r2",
                "addresses": [],
                "target_dep": "eslint-plugin-react",
                "strategy": "bump",
                "to_range": "^8.0.0",
                "status": "failed",
                "verification": verification,
            },
        },
        "verified_workdirs": {
            "eslint": work_dir,
            "eslint-plugin-react": work_dir,
        },
    }
    result = await pr_and_persist_node(state, config)

    git_pr.open_pr.assert_not_called()
    assert result == {"remediation_result_id": "rid-1"}
    assert not os.path.exists(mkdtemp_root)
    remediation = dao.save_remediation.await_args.args[0]
    assert all(r.pr_url is None for r in remediation.remediations)


@pytest.mark.asyncio
async def test_pr_and_persist_node_opens_one_pr_when_consent_true():
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep(repo_path="/original/repo"))
    dao.save_remediation = AsyncMock(return_value="rid-1")
    git_pr = AsyncMock()
    git_pr.open_pr = AsyncMock(return_value="https://gh/pr/1")
    config = {
        "configurable": {
            "result_dao": dao,
            "container": MagicMock(),
            "remediate": True,
            "git_pr": git_pr,
        }
    }

    # A real dst/repo-shaped temp dir matching copy_repo's actual contract
    # -- pr_and_persist_node cleans up via
    # shutil.rmtree(os.path.dirname(work_dir)), so a test double shaped any
    # other way (e.g. a bare tmp_path, not tmp_path/repo) would make that
    # cleanup target something far too broad, like a shared pytest tmp root.
    # This dir is now pre-verified by group_and_verify_gate (Task 2) --
    # pr_and_persist_node must not touch its contents, only ship it.
    mkdtemp_root = tempfile.mkdtemp(prefix="test-remediation-")
    work_dir = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work_dir)

    state = {
        "job_id": "job-1",
        "prep_result_id": "prep-1",
        "remediations": {
            "lodash": {
                "id": "r1",
                "addresses": ["lodash"],
                "target_dep": "lodash",
                "strategy": "bump",
                "to_range": "^4.17.21",
                "status": "fixed",
                "verification": {"installed": True, "finding_resolved": True},
            }
        },
        "verified_workdirs": {"lodash": work_dir},
    }
    result = await pr_and_persist_node(state, config)

    git_pr.open_pr.assert_awaited_once()
    branch = git_pr.open_pr.await_args.args[1]
    assert branch == "remediation/job-1-lodash"
    assert result == {"remediation_result_id": "rid-1"}
    # Cleanup must target the mkdtemp root, not something broader.
    assert not os.path.exists(mkdtemp_root)


@pytest.mark.asyncio
async def test_pr_and_persist_node_groups_shared_workdir_into_one_pr():
    """Two deps whose verified_workdirs entries point at the SAME path (a
    coupled group group_and_verify_gate already verified together) must
    ship as one PR, not two."""
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep(repo_path="/original/repo"))
    dao.save_remediation = AsyncMock(return_value="rid-1")
    git_pr = AsyncMock()
    git_pr.open_pr = AsyncMock(return_value="https://gh/pr/1")
    config = {
        "configurable": {
            "result_dao": dao,
            "container": MagicMock(),
            "remediate": True,
            "git_pr": git_pr,
        }
    }

    mkdtemp_root = tempfile.mkdtemp(prefix="test-remediation-")
    work_dir = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work_dir)

    verification = {"installed": True, "finding_resolved": True}
    state = {
        "job_id": "job-1",
        "prep_result_id": "prep-1",
        "remediations": {
            "eslint": {
                "id": "r1",
                "addresses": ["eslint"],
                "target_dep": "eslint",
                "strategy": "bump",
                "to_range": "^9.0.0",
                "status": "fixed",
                "verification": verification,
            },
            "eslint-plugin-react": {
                "id": "r2",
                "addresses": [],
                "target_dep": "eslint-plugin-react",
                "strategy": "bump",
                "to_range": "^8.0.0",
                "status": "fixed",
                "verification": verification,
            },
        },
        "verified_workdirs": {
            "eslint": work_dir,
            "eslint-plugin-react": work_dir,
        },
    }
    result = await pr_and_persist_node(state, config)

    git_pr.open_pr.assert_awaited_once()
    assert result == {"remediation_result_id": "rid-1"}
    remediation = dao.save_remediation.await_args.args[0]
    by_dep = {r.target_dep: r for r in remediation.remediations}
    assert by_dep["eslint"].pr_url == "https://gh/pr/1"
    assert by_dep["eslint-plugin-react"].pr_url == "https://gh/pr/1"
    assert by_dep["eslint"].branch == by_dep["eslint-plugin-react"].branch


def test_pr_title_and_body_bump_case():
    members = [
        Remediation(
            id="r1",
            addresses=["lodash"],
            finding_summaries=[
                FindingSummary(
                    dep_name="lodash",
                    severity="high",
                    description="known prototype pollution vulnerability",
                )
            ],
            target_dep="lodash",
            strategy="bump",
            from_range="^4.17.11",
            to_range="^4.17.21",
            status="fixed",
        )
    ]
    verification = VerificationResult(
        installed=True, tested=True, finding_resolved=True
    )

    title, body = _pr_title_and_body(members, verification)

    assert "Automated dependency remediation" not in body
    assert body.startswith("## Summary")
    assert title == "Remediate lodash (bump)"
    assert "please review before merging" not in body
    assert "| lodash | bump | `^4.17.11` -> `^4.17.21` | - |" in body
    assert (
        "| lodash | high | known prototype pollution vulnerability | lodash |" in body
    )
    assert "- [x] Install" in body
    assert "- [x] Tests" in body
    assert "- [x] Audit re-scan: finding no longer present" in body
    assert "## Migration notes" not in body


def test_pr_findings_table_truncates_long_description():
    long_desc = "x" * 300
    members = [
        Remediation(
            addresses=["lodash"],
            finding_summaries=[
                FindingSummary(
                    dep_name="lodash", severity="high", description=long_desc
                )
            ],
            target_dep="lodash",
        )
    ]
    table = _pr_findings_table(members)
    row = next(line for line in table.splitlines() if line.startswith("| lodash"))
    cell = row.split(" | ")[2]
    assert len(cell) <= 150
    assert cell.endswith("…")


def test_pr_findings_table_escapes_pipe_in_description():
    members = [
        Remediation(
            addresses=["lodash"],
            finding_summaries=[
                FindingSummary(
                    dep_name="lodash",
                    severity="high",
                    description=(
                        "Prototype pollution | affects parse() | CVE-2021-44906"
                    ),
                )
            ],
            target_dep="lodash",
        )
    ]
    table = _pr_findings_table(members)
    row = next(line for line in table.splitlines() if line.startswith("| lodash"))
    # Exactly 4 cells: Finding, Severity, Description, Resolved by.
    assert row.count(" | ") == 3
    assert "\\|" in row


def test_pr_findings_table_none_when_no_addresses():
    # `_pr_findings_table` falls back to `r.target_dep` when `addresses` is
    # empty (pre-existing behavior, unchanged by this task), so a member with
    # addresses=[] still yields a row. The "None." branch is only reachable
    # with an empty group.
    members: list[Remediation] = []
    assert _pr_findings_table(members) == "None."


def test_pr_findings_table_dash_when_no_summary_for_finding():
    members = [Remediation(addresses=["lodash"], target_dep="lodash")]
    table = _pr_findings_table(members)
    assert "| lodash | - | - | lodash |" in table


def test_pr_verification_summary_all_passed():
    v = VerificationResult(
        installed=True, built=True, tested=True, finding_resolved=True
    )
    summary = _pr_verification_summary(v)
    assert summary == (
        "- [x] Install\n"
        "- [x] Build\n"
        "- [x] Tests\n"
        "- [x] Audit re-scan: finding no longer present"
    )


def test_pr_verification_summary_failure_and_omitted_fields():
    v = VerificationResult(
        installed=True, built=None, tested=False, finding_resolved=None
    )
    summary = _pr_verification_summary(v)
    assert summary == "- [x] Install\n- [ ] Tests (failed)"


def test_pr_title_and_body_replace_case_includes_migration_notes():
    members = [
        Remediation(
            id="r1",
            addresses=["left-pad", "old-transitive"],
            target_dep="left-pad",
            strategy="replace",
            replacement_dep="pad-string",
            replacement_range="^2.0.0",
            migration_plan="swap default import for the named `pad` export",
            status="fixed",
        )
    ]
    verification = VerificationResult(installed=True, finding_resolved=False)

    title, body = _pr_title_and_body(members, verification)

    assert title == "Remediate left-pad (replace - review required)"
    assert "please review before merging" in body
    assert "replaced with `pad-string@^2.0.0`" in body
    assert "| left-pad | - | - | left-pad |" in body
    assert "| old-transitive | - | - | left-pad |" in body
    assert "- [ ] Audit re-scan (failed)" not in body
    assert "- [ ] Audit re-scan: finding still present" in body
    assert "## Migration notes" in body
    assert "swap default import for the named `pad` export" in body
