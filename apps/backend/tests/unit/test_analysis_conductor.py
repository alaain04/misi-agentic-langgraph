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
