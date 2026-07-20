from __future__ import annotations

from src.models.results import EvidenceBundle


def _bundle(note=None):
    return EvidenceBundle(
        domain="vulnerabilities",
        hypothesis="h",
        packages_to_focus=["express"],
        findings=[],
        summary="s",
        confidence=0.2,
        verification_note=note,
    )


def test_format_bundles_shows_verification_note():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import (
        _format_bundles,
    )

    rendered = _format_bundles([_bundle(note="express finding unsupported")])
    assert "unresolved: express finding unsupported" in rendered


def test_format_bundles_omits_note_when_none():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import (
        _format_bundles,
    )

    rendered = _format_bundles([_bundle(note=None)])
    assert "unresolved:" not in rendered


def test_system_prompt_mentions_flagged_bundles():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import _build_system

    system = _build_system(4)
    assert "unresolved" in system.lower()


def test_system_prompt_mentions_license_agent_dispatch_strategy():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import _build_system

    system = _build_system(4)
    # appears once via the auto-generated roster, and once more via the
    # explicit dispatch-strategy line -- proves the guidance line was added,
    # not just agent registration
    assert system.count("license_agent") >= 2
    assert "never shard it" in system


def _dispatch(agent_type: str, hypothesis: str = "h"):
    from src.models.results import AgentDispatch

    return AgentDispatch(
        domain="d", hypothesis=hypothesis, packages_to_focus=[], agent_type=agent_type
    )


def test_drop_repeat_whole_tree_dispatch_already_run():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import (
        drop_repeat_whole_tree_dispatches,
    )

    agent_calls = [{"agent_type": "vulnerability_agent"}]
    dispatches = [_dispatch("vulnerability_agent"), _dispatch("maintenance_agent")]
    result = drop_repeat_whole_tree_dispatches(dispatches, agent_calls)
    assert [d.agent_type for d in result] == ["maintenance_agent"]


def test_drop_repeat_whole_tree_dispatch_same_round_duplicate():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import (
        drop_repeat_whole_tree_dispatches,
    )

    dispatches = [
        _dispatch("license_agent", "angle A"),
        _dispatch("license_agent", "angle B"),
    ]
    result = drop_repeat_whole_tree_dispatches(dispatches, [])
    assert len(result) == 1
    assert result[0].agent_type == "license_agent"


def test_does_not_cap_package_scoped_agent():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import (
        drop_repeat_whole_tree_dispatches,
    )

    agent_calls = [{"agent_type": "maintenance_agent"}]
    dispatches = [_dispatch("maintenance_agent")]
    result = drop_repeat_whole_tree_dispatches(dispatches, agent_calls)
    assert len(result) == 1  # package-scoped agents are not capped here


def test_keeps_novel_whole_tree_dispatch():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import (
        drop_repeat_whole_tree_dispatches,
    )

    result = drop_repeat_whole_tree_dispatches([_dispatch("vulnerability_agent")], [])
    assert len(result) == 1
