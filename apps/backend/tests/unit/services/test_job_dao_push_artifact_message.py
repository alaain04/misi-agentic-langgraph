"""Unit test: push_artifact_message contract via the abstract port."""

from src.domain.ports.job_repository_port import JobRepositoryPort
import inspect


def test_push_artifact_message_is_on_port():
    members = {name for name, _ in inspect.getmembers(JobRepositoryPort)}
    assert "push_artifact_message" in members


def test_push_proposal_removed_from_port():
    members = {name for name, _ in inspect.getmembers(JobRepositoryPort)}
    assert "push_proposal" not in members
    assert "update_proposal" not in members
