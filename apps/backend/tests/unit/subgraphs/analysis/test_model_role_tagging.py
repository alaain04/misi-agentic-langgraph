import importlib


def _tags_of(module_path: str, attr: str) -> list[str]:
    module = importlib.import_module(module_path)
    obj = getattr(module, attr)
    return obj.config.get("tags", [])


def test_understand_concern_tagged_correctly():
    tags = _tags_of(
        "src.main_graph.subgraphs.analysis.nodes.understand_concern", "_llm"
    )
    assert "agent_role:understand_concern" in tags


def test_analysis_dispatch_tagged_correctly():
    tags = _tags_of(
        "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper", "_llm"
    )
    assert "agent_role:analysis_dispatch" in tags


def test_coverage_judge_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.analysis.deepagent.coverage", "_llm")
    assert "agent_role:coverage_judge" in tags


def test_specialist_agent_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.analysis.agents.base_agent", "_llm")
    assert "agent_role:specialist_agent" in tags


def test_analysis_critique_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.analysis.agents.critique", "_llm")
    assert "agent_role:analysis_critique" in tags
