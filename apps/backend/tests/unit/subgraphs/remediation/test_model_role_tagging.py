import importlib


def _tags_of(module_path: str, attr: str = "_llm") -> list[str]:
    module = importlib.import_module(module_path)
    return list(getattr(module, attr).tags or [])


def test_remediation_release_research_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.remediation.release_research")
    assert "agent_role:remediation_release_research" in tags


def test_remediation_plan_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.remediation.plan")
    assert "agent_role:remediation_plan" in tags


def test_remediation_execution_deepagent_tagged_correctly():
    # This site hands its model to create_deep_agent, so the tag has to ride
    # on the model instance handed over -- there is no module-level _llm.
    from unittest.mock import MagicMock, patch

    from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
        build_execution_agent,
    )

    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.create_deep_agent"
    ) as mock_create:
        build_execution_agent(
            work_dir="/tmp/does-not-need-to-exist",
            container=MagicMock(),
            docker_image="irrelevant:latest",
            package_manager="npm",
        )

    model = mock_create.call_args.kwargs["model"]
    assert "agent_role:remediation_execution_deepagent" in (model.tags or [])


def test_execution_deepagent_compiles_with_the_tagged_model_unmocked():
    # The tagged model must remain acceptable to the real
    # deepagents.create_deep_agent -- it rejects anything that is not a
    # BaseChatModel, which is what ruled out the old .with_config() wrapping.
    from unittest.mock import MagicMock

    from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
        build_execution_agent,
    )

    agent = build_execution_agent(
        work_dir="/tmp/does-not-need-to-exist",
        container=MagicMock(),
        docker_image="irrelevant:latest",
        package_manager="npm",
    )
    assert agent is not None
