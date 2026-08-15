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


def test_resolve_model_rejects_unknown_override_key(monkeypatch):
    # A typo'd role KEY used to be silently ignored (resolving to the default
    # and quietly invalidating the experiment), unlike a typo'd model VALUE.
    monkeypatch.setattr(
        settings,
        "model_overrides",
        {"specialst_agent": "gpt-5.4-nano-2026-03-17"},
    )
    import pytest

    with pytest.raises(ValueError, match="specialst_agent"):
        resolve_model(AgentRole.SPECIALIST_AGENT)


def test_unknown_override_key_is_rejected_even_for_an_unrelated_role(monkeypatch):
    # The whole dict is validated, not just the key being looked up -- the
    # misspelled role is by definition never the one resolved.
    monkeypatch.setattr(
        settings, "model_overrides", {"specialst_agent": "gpt-5.4-nano-2026-03-17"}
    )
    import pytest

    with pytest.raises(ValueError):
        resolve_model(AgentRole.COVERAGE_JUDGE)


def test_get_role_llm_tags_the_model_instance_with_its_role():
    # The tag must live on the model instance (BaseChatModel.tags), not on a
    # surrounding RunnableBinding: 11 of the 14 call sites immediately call
    # .with_structured_output(), which discards a binding's config.
    llm = get_role_llm(AgentRole.REMEDIATION_PLAN)
    assert "agent_role:remediation_plan" in (llm.tags or [])


def test_get_role_llm_returns_a_real_base_chat_model():
    # create_deep_agent(model=...) rejects anything that is not a
    # BaseChatModel, which is why .with_config() wrapping was removed.
    from langchain_core.language_models import BaseChatModel

    assert isinstance(get_role_llm(AgentRole.ANALYSIS_ROOT_DEEPAGENT), BaseChatModel)


def test_role_tag_survives_with_structured_output():
    from pydantic import BaseModel

    class _Out(BaseModel):
        answer: str

    chain = get_role_llm(AgentRole.SPECIALIST_AGENT).with_structured_output(_Out)
    bound = chain.first  # the model step of the structured-output sequence
    assert "agent_role:specialist_agent" in (bound.tags or [])


def test_remediation_release_research_role_exists_and_classify_role_removed():
    assert (
        AgentRole.REMEDIATION_RELEASE_RESEARCH.value == "remediation_release_research"
    )
    assert not hasattr(AgentRole, "REMEDIATION_CLASSIFY")
