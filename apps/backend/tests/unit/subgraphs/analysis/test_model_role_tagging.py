import importlib


def _tags_of(module_path: str, attr: str) -> list[str]:
    module = importlib.import_module(module_path)
    obj = getattr(module, attr)
    return list(obj.tags or [])


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


def test_analysis_root_deepagent_tagged_correctly():
    # This site hands its model to create_deep_agent, so the tag has to ride
    # on the model instance handed over -- there is no module-level _llm.
    from unittest.mock import patch

    from src.main_graph.subgraphs.analysis.deepagent import nodes

    with patch.object(nodes, "create_deep_agent") as mock_create:
        nodes._build_deep_agent()

    model = mock_create.call_args.kwargs["model"]
    assert "agent_role:analysis_root_deepagent" in (model.tags or [])


def test_root_deepagent_compiles_with_the_tagged_model_unmocked():
    # The real deepagents.create_deep_agent rejects anything that is not a
    # BaseChatModel -- which is what ruled out the old .with_config() wrapping.
    from src.main_graph.subgraphs.analysis.deepagent import nodes

    assert nodes._build_deep_agent() is not None
