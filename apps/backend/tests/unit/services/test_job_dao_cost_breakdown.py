import inspect

from src.domain.ports.job_repository_port import JobRepositoryPort


def test_save_cost_breakdown_is_on_port():
    members = {name for name, _ in inspect.getmembers(JobRepositoryPort)}
    assert "save_cost_breakdown" in members
