import importlib


def _tags_of(module_path: str, attr: str = "_llm") -> list[str]:
    module = importlib.import_module(module_path)
    return getattr(module, attr).config.get("tags", [])


def test_report_synthesizer_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.report.nodes.report_synthesizer")
    assert "agent_role:report_synthesizer" in tags


def test_finding_enricher_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.report.agents.finding_enricher_agent")
    assert "agent_role:finding_enricher" in tags


def test_impact_analysis_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.report.agents.impact_analysis_agent")
    assert "agent_role:impact_analysis" in tags


def test_report_critique_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.report.agents.critique")
    assert "agent_role:report_critique" in tags
