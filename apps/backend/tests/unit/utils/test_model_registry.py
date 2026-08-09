from src.utils.config import settings
from src.utils.llm import Model
from src.utils.model_registry import AgentRole, get_role_llm, resolve_model


def test_resolve_model_defaults_to_gpt_5_4_mini_for_every_role():
    for role in AgentRole:
        assert resolve_model(role) is Model.GPT_5_4_MINI


def test_resolve_model_honors_override(monkeypatch):
    monkeypatch.setattr(
        settings,
        "model_overrides",
        {"specialist_agent": "gpt-5.4-nano-2026-03-17"},
    )
    assert resolve_model(AgentRole.SPECIALIST_AGENT) is Model.GPT_5_4_NANO
    assert resolve_model(AgentRole.COVERAGE_JUDGE) is Model.GPT_5_4_MINI


def test_resolve_model_rejects_unknown_override_value(monkeypatch):
    monkeypatch.setattr(
        settings, "model_overrides", {"specialist_agent": "not-a-real-model"}
    )
    import pytest

    with pytest.raises(ValueError):
        resolve_model(AgentRole.SPECIALIST_AGENT)


def test_get_role_llm_tags_the_runnable_with_its_role():
    llm = get_role_llm(AgentRole.REMEDIATION_PLAN)
    assert "agent_role:remediation_plan" in llm.config.get("tags", [])
