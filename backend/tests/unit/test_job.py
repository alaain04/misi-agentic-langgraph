from datetime import UTC, datetime

from src.models.job import Job, JobStatus


def test_job_defaults():
    job = Job(repo_url="https://github.com/org/repo", concern="security")
    assert job.status == JobStatus.pending
    assert isinstance(job.id, str) and len(job.id) == 24
    assert job.token is None
    assert isinstance(job.created_at, datetime)
    assert job.created_at.tzinfo == UTC


def test_job_to_doc_renames_id():
    job = Job(repo_url="https://github.com/org/repo", concern="perf")
    doc = job.to_doc()
    assert "_id" in doc
    assert "id" not in doc
    assert doc["_id"] == job.id


def test_job_to_doc_contains_all_fields():
    job = Job(repo_url="https://github.com/org/repo", concern="bugs", token="tok123")
    doc = job.to_doc()
    assert doc["repo_url"] == "https://github.com/org/repo"
    assert doc["concern"] == "bugs"
    assert doc["token"] == "tok123"
    assert doc["status"] == JobStatus.pending


def test_job_unique_ids():
    a = Job(repo_url="https://github.com/org/repo", concern="x")
    b = Job(repo_url="https://github.com/org/repo", concern="x")
    assert a.id != b.id
