from __future__ import annotations

from unittest.mock import MagicMock

from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
    build_codemod_subagent,
    build_replacement_subagent,
)


def test_build_codemod_subagent_shape():
    sub = build_codemod_subagent("/tmp/work", MagicMock(), "img", "npm")
    assert sub["name"] == "codemod_adapter"
    assert "runnable" in sub and sub["description"]


def test_build_replacement_subagent_shape():
    sub = build_replacement_subagent("/tmp/work", MagicMock(), "img", "npm")
    assert sub["name"] == "replacement_migrator"
    assert "runnable" in sub and sub["description"]
