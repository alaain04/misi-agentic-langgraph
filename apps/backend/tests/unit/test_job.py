from datetime import UTC, datetime

from src.models.job import Job, JobMetadata, JobStatus

_REPO_URL = "https://github.com/example/repo"


def test_job_defaults():
    job = Job(metadata=JobMetadata(repo_url=_REPO_URL, concern="security"))
    assert job.status == JobStatus.pending
    assert isinstance(job.id, str) and len(job.id) == 24
    assert isinstance(job.created_at, datetime)
    assert job.created_at.tzinfo == UTC


def test_job_to_doc_renames_id():
    job = Job(metadata=JobMetadata(repo_url=_REPO_URL, concern="perf"))
    doc = job.to_doc()
    assert "_id" in doc
    assert "id" not in doc
    assert doc["_id"] == job.id


def test_job_to_doc_contains_all_fields():
    job = Job(metadata=JobMetadata(repo_url=_REPO_URL, concern="bugs"))
    doc = job.to_doc()
    assert doc["metadata"]["concern"] == "bugs"
    assert doc["status"] == JobStatus.pending


def test_job_unique_ids():
    a = Job(metadata=JobMetadata(repo_url=_REPO_URL, concern="x"))
    b = Job(metadata=JobMetadata(repo_url=_REPO_URL, concern="x"))
    assert a.id != b.id


def test_job_dao_implements_port():
    from src.domain.ports.job_repository_port import JobRepositoryPort
    from src.services.job_dao import JobDAO

    assert issubclass(JobDAO, JobRepositoryPort)


def test_job_dao_implements_push_artifact_item():
    import inspect

    from src.domain.ports.job_repository_port import JobRepositoryPort
    from src.services.job_dao import JobDAO

    assert hasattr(JobDAO, "push_artifact_item")
    assert inspect.iscoroutinefunction(JobDAO.push_artifact_item)
    # Port must declare it too
    assert "push_artifact_item" in {m for m in dir(JobRepositoryPort) if not m.startswith("_")}
