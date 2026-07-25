import pytest

from src.main_graph.subgraphs.remediation.nodes.remediate import remediate
from src.models.conductor import FindingNote
from src.models.remediation import Remediation
from src.models.results import AnalysisResult, PrepResult


class FakeDao:
    def __init__(self, analysis, prep):
        self._a, self._p = analysis, prep
        self.saved = None

    async def get_analysis(self, _id):
        return self._a

    async def get_prep(self, _id):
        return self._p

    async def save_remediation(self, result):
        self.saved = result
        return "rem-1"


def _prep():
    return PrepResult(
        job_id="j1", repo_path="/tmp/does-not-matter", project_metadata={},
        manifest_files=["package.json"], detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.11"}, "packages": {}},
        discovery_summary="", vector_store_id="",
    )


def _analysis():
    return AnalysisResult(
        job_id="j1", concern="c",
        findings=[
            FindingNote(
                dep_name="lodash", severity="high", description="cve", evidence=[]
            )
        ],
        evidence_bundle_ids=[], iteration_count=1,
    )


@pytest.mark.asyncio
async def test_node_persists_result_and_skips_pr_without_consent(monkeypatch):
    dao = FakeDao(_analysis(), _prep())
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.copy_repo",
        lambda p: "/tmp/work")
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.shutil.rmtree",
        lambda *a, **k: None)
    async def fake_audit(*a, **k):
        return {"vulnerabilities": {}}
    async def fake_outdated(*a, **k):
        return {"outdated": {}}
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.npm_audit",
        fake_audit)
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.npm_outdated",
        fake_outdated)
    async def fake_run(*a, **k):
        return [Remediation(
            addresses=["lodash"], target_dep="lodash", strategy="bump",
            to_range="^4.17.21", status="fixed", patch="P",
        )]
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.run_remediation",
        fake_run)

    config = {"configurable": {"result_dao": dao, "container": object(),
                               "remediate": False, "git_pr": None}}
    out = await remediate(
        {"job_id": "j1", "concern": "c", "prep_result_id": "p",
         "analysis_result_id": "a"},
        config)
    assert out["remediation_result_id"] == "rem-1"
    assert dao.saved.consent is False and dao.saved.pr_url is None
    assert dao.saved.remediations[0].status == "fixed"


@pytest.mark.asyncio
async def test_node_opens_pr_with_consent(monkeypatch):
    dao = FakeDao(_analysis(), _prep())
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.copy_repo",
        lambda p: "/tmp/work")
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.shutil.rmtree",
        lambda *a, **k: None)
    async def fake_audit(*a, **k):
        return {}
    async def fake_outdated(*a, **k):
        return {}
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.npm_audit",
        fake_audit)
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.npm_outdated",
        fake_outdated)
    async def fake_run(*a, **k):
        return [Remediation(
            addresses=["lodash"], target_dep="lodash", strategy="bump",
            to_range="^4.17.21", status="fixed", patch="P",
        )]
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.run_remediation",
        fake_run)

    class FakePR:
        async def open_pr(self, work_dir, branch, title, body):
            self.branch = branch
            return "https://gh/pull/9"

    pr = FakePR()
    config = {"configurable": {"result_dao": dao, "container": object(),
                               "remediate": True, "git_pr": pr}}
    await remediate({"job_id": "job12345", "concern": "c",
                     "prep_result_id": "p", "analysis_result_id": "a"}, config)
    assert dao.saved.pr_url == "https://gh/pull/9"
    assert dao.saved.consent is True and pr.branch.startswith("remediation/")
