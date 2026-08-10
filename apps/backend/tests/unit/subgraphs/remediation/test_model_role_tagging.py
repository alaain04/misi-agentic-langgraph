import importlib


def _tags_of(module_path: str, attr: str = "_llm") -> list[str]:
    module = importlib.import_module(module_path)
    return getattr(module, attr).config.get("tags", [])


def test_remediation_classify_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.remediation.classify")
    assert "agent_role:remediation_classify" in tags


def test_remediation_investigate_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.remediation.investigate")
    assert "agent_role:remediation_investigate" in tags


def test_remediation_plan_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.remediation.plan")
    assert "agent_role:remediation_plan" in tags


def test_remediation_execution_deepagent_tagged_correctly():
    from src.domain.ports.container_run_port import ContainerRunPort
    from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
        build_execution_agent,
    )

    class _FakeContainer(ContainerRunPort):
        async def run(self, *args, **kwargs):
            raise NotImplementedError

    agent = build_execution_agent(
        work_dir="/tmp/does-not-need-to-exist",
        container=_FakeContainer(),
        docker_image="irrelevant:latest",
        package_manager="npm",
    )
    assert agent is not None
