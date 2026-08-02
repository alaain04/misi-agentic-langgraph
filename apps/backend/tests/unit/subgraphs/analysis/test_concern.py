from __future__ import annotations

from src.main_graph.subgraphs.analysis.concern import (
    Concern,
    ConcernDraft,
    invalid_concern,
    is_simple,
    packages_valid,
    route_after_understand_concern,
    route_concern,
    whole_tree_agents,
)


def _concern(**overrides) -> Concern:
    defaults = dict(
        is_valid=True,
        type=["vulnerability"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=["vulnerability_agent"],
    )
    defaults.update(overrides)
    return Concern(**defaults)


def test_vulnerability_only_is_simple():
    assert is_simple(_concern(type=["vulnerability"])) is True


def test_license_only_is_simple():
    assert (
        is_simple(_concern(type=["license"], preferred_agents=["license_agent"]))
        is True
    )


def test_vulnerability_and_license_is_simple():
    concern = _concern(
        type=["vulnerability", "license"],
        preferred_agents=["vulnerability_agent", "license_agent"],
    )
    assert is_simple(concern) is True


def test_maintenance_type_forces_complex():
    concern = _concern(type=["maintenance"], preferred_agents=["maintenance_agent"])
    assert is_simple(concern) is False


def test_mixed_simple_and_complex_type_forces_complex():
    assert is_simple(_concern(type=["vulnerability", "maintenance"])) is False


def test_requires_per_dependency_analysis_forces_complex():
    assert is_simple(_concern(requires_per_dependency_analysis=True)) is False


def test_specific_packages_scope_forces_complex():
    concern = _concern(scope="specific_packages", packages=["lodash"])
    assert is_simple(concern) is False


def test_route_concern_returns_simple():
    state = {"structured_concern": _concern().model_dump()}
    assert route_concern(state) == "simple"


def test_route_concern_returns_complex():
    concern = _concern(type=["maintenance"], preferred_agents=["maintenance_agent"])
    state = {"structured_concern": concern.model_dump()}
    assert route_concern(state) == "complex"


def test_whole_tree_agents_returns_preferred_whole_tree_agents():
    concern = _concern(
        type=["vulnerability", "license"],
        preferred_agents=["vulnerability_agent", "license_agent"],
    )
    assert whole_tree_agents(concern) == ["vulnerability_agent", "license_agent"]


def test_whole_tree_agents_excludes_non_whole_tree_agents():
    concern = _concern(
        type=["vulnerability", "maintenance"],
        preferred_agents=["vulnerability_agent", "maintenance_agent"],
    )
    assert whole_tree_agents(concern) == ["vulnerability_agent"]


def test_whole_tree_agents_empty_when_no_whole_tree_type_present():
    concern = _concern(type=["maintenance"], preferred_agents=["maintenance_agent"])
    assert whole_tree_agents(concern) == []


def test_whole_tree_agents_empty_when_scope_is_specific_packages():
    concern = _concern(
        type=["vulnerability"],
        scope="specific_packages",
        packages=["lodash"],
        preferred_agents=["vulnerability_agent"],
    )
    assert whole_tree_agents(concern) == []


def test_route_after_understand_concern_returns_valid():
    state = {"structured_concern": _concern().model_dump()}
    assert route_after_understand_concern(state) == "valid"


def test_route_after_understand_concern_returns_invalid():
    state = {"structured_concern": _concern(is_valid=False).model_dump()}
    assert route_after_understand_concern(state) == "invalid"


def test_route_after_understand_concern_defaults_to_valid_when_missing():
    state = {"structured_concern": {}}
    assert route_after_understand_concern(state) == "valid"


def test_packages_valid_all_known():
    assert (
        packages_valid(["lodash", "react"], {"lodash": "4.17.20", "react": "18.2.0"})
        is True
    )


def test_packages_valid_one_unknown():
    assert packages_valid(["lodash", "left-pad"], {"lodash": "4.17.20"}) is False


def test_packages_valid_empty_list_is_valid():
    assert packages_valid([], {"lodash": "4.17.20"}) is True


def test_invalid_concern_matches_placeholder_contract():
    concern = invalid_concern()
    assert concern.is_valid is False
    assert concern.type == ["other"]
    assert concern.scope == "all_dependencies"
    assert concern.packages == []
    assert concern.requires_per_dependency_analysis is False
    assert concern.preferred_agents == []


def test_concern_draft_has_no_scope_or_preferred_agents_fields():
    draft = ConcernDraft(
        is_valid=True,
        type=["vulnerability"],
        packages=[],
        requires_per_dependency_analysis=False,
    )
    assert not hasattr(draft, "scope")
    assert not hasattr(draft, "preferred_agents")
