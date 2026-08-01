from __future__ import annotations

from src.main_graph.subgraphs.analysis.concern import Concern, is_simple, route_concern


def _concern(**overrides) -> Concern:
    defaults = dict(
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
