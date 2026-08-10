import asyncio

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from src.utils.cost import CostCallback


def _usage_message(prompt_tokens: int, completion_tokens: int, **kwargs) -> AIMessage:
    return AIMessage(
        content="ok",
        response_metadata={
            "token_usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
            "model_name": "gpt-5.4-mini-2026-03-17",
        },
        **kwargs,
    )


def _fake_llm_with_usage(
    role: str, prompt_tokens: int, completion_tokens: int, responses: int = 1
):
    # Tags go on the model instance, exactly like get_role_llm() sets them --
    # NOT via .with_config(), which produces a RunnableBinding whose tag is
    # silently dropped by later composition (see the structured-output test).
    return GenericFakeChatModel(
        messages=iter(
            [_usage_message(prompt_tokens, completion_tokens) for _ in range(responses)]
        ),
        tags=[f"agent_role:{role}"],
    )


def test_breakdown_buckets_cost_and_tokens_by_role_tag():
    cb = CostCallback()
    llm_a = _fake_llm_with_usage("specialist_agent", 1000, 500)
    llm_b = _fake_llm_with_usage("coverage_judge", 2000, 1000)

    asyncio.run(llm_a.ainvoke("hi", config={"callbacks": [cb]}))
    asyncio.run(llm_b.ainvoke("hi", config={"callbacks": [cb]}))

    breakdown = cb.breakdown()
    assert set(breakdown) == {"specialist_agent", "coverage_judge"}
    assert breakdown["specialist_agent"]["prompt_tokens"] == 1000
    assert breakdown["specialist_agent"]["completion_tokens"] == 500
    assert breakdown["specialist_agent"]["call_count"] == 1
    assert breakdown["specialist_agent"]["cost"] > 0
    assert breakdown["coverage_judge"]["prompt_tokens"] == 2000


def test_breakdown_sums_multiple_calls_for_the_same_role():
    cb = CostCallback()
    llm = _fake_llm_with_usage("remediation_plan", 100, 50)
    asyncio.run(llm.ainvoke("hi", config={"callbacks": [cb]}))
    llm2 = _fake_llm_with_usage("remediation_plan", 100, 50)
    asyncio.run(llm2.ainvoke("hi", config={"callbacks": [cb]}))

    assert cb.breakdown()["remediation_plan"]["call_count"] == 2
    assert cb.breakdown()["remediation_plan"]["prompt_tokens"] == 200


def test_breakdown_buckets_untagged_calls_separately():
    cb = CostCallback()
    llm = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="ok",
                    response_metadata={
                        "token_usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                        },
                        "model_name": "gpt-5.4-mini-2026-03-17",
                    },
                )
            ]
        )
    )
    asyncio.run(llm.ainvoke("hi", config={"callbacks": [cb]}))
    assert "untagged" in cb.breakdown()


def test_breakdown_records_latency_ms_per_role():
    cb = CostCallback()
    llm = _fake_llm_with_usage("understand_concern", 10, 5)
    asyncio.run(llm.ainvoke("hi", config={"callbacks": [cb]}))
    assert cb.breakdown()["understand_concern"]["latency_ms"] >= 0


def test_total_cost_and_tokens_unchanged_by_breakdown_tracking():
    cb = CostCallback()
    llm = _fake_llm_with_usage("specialist_agent", 1000, 500)
    asyncio.run(llm.ainvoke("hi", config={"callbacks": [cb]}))
    assert cb.total_tokens == 1500
    assert cb.cost() > 0


def test_breakdown_records_the_model_each_role_ran_on():
    cb = CostCallback()
    llm = _fake_llm_with_usage("impact_analysis", 10, 5)
    asyncio.run(llm.ainvoke("hi", config={"callbacks": [cb]}))
    assert cb.breakdown()["impact_analysis"]["model"] == "gpt-5.4-mini-2026-03-17"


def test_every_model_has_pricing():
    # A new Model enum member without a _PRICING entry would silently bill at
    # _FALLBACK_RATE and quietly corrupt every cost figure in the thesis data.
    from src.utils.cost import _PRICING
    from src.utils.llm import Model

    missing = [m.value for m in Model if m.value not in _PRICING]
    assert not missing, f"Model(s) missing a _PRICING entry: {missing}"


def test_role_tag_survives_structured_output_and_is_attributed():
    # The regression this whole mechanism turns on: 12 of the 14 call sites do
    # get_role_llm(role).with_structured_output(...). A tag bound with
    # .with_config() is dropped there (RunnableBindingBase.__getattr__
    # delegates to the wrapped model), so those calls landed in "untagged".
    # An instance-level tag -- what get_role_llm now sets -- survives.
    # Exercised through real LangChain composition, not a hand-built tag list.
    from pydantic import BaseModel

    class _Out(BaseModel):
        answer: str

    class _ToolCallingFake(GenericFakeChatModel):
        """GenericFakeChatModel has no bind_tools, which
        BaseChatModel.with_structured_output needs."""

        def bind_tools(self, tools, **kwargs):
            return self.bind(tools=list(tools), **kwargs)

    def _tool_call_response():
        return _usage_message(
            100,
            50,
            tool_calls=[{"name": "_Out", "args": {"answer": "42"}, "id": "call_1"}],
        )

    tagged = CostCallback()
    instance_tagged = _ToolCallingFake(
        messages=iter([_tool_call_response()]),
        tags=["agent_role:remediation_plan"],
    )
    result = instance_tagged.with_structured_output(_Out).invoke(
        "q", config={"callbacks": [tagged]}
    )
    assert result.answer == "42"
    assert set(tagged.breakdown()) == {"remediation_plan"}

    # And the old .with_config() mechanism demonstrably does NOT survive --
    # this asserts the bug is real, so the test fails if someone reintroduces it.
    bound = CostCallback()
    config_tagged = _ToolCallingFake(
        messages=iter([_tool_call_response()])
    ).with_config(tags=["agent_role:remediation_plan"])
    config_tagged.with_structured_output(_Out).invoke(
        "q", config={"callbacks": [bound]}
    )
    assert set(bound.breakdown()) == {"untagged"}


def test_two_tagged_models_get_separate_buckets_through_real_invocation():
    cb = CostCallback()
    root = _fake_llm_with_usage("analysis_root_deepagent", 100, 50)
    child = _fake_llm_with_usage("analysis_dispatch", 20, 10)

    root.invoke("root question", config={"callbacks": [cb]})
    child.invoke("child question", config={"callbacks": [cb]})

    breakdown = cb.breakdown()
    assert set(breakdown) == {"analysis_root_deepagent", "analysis_dispatch"}
    assert breakdown["analysis_root_deepagent"]["prompt_tokens"] == 100
    assert breakdown["analysis_dispatch"]["prompt_tokens"] == 20


def test_nested_graph_attributes_child_model_to_its_own_role():
    # The deep-agent shape: a compiled graph whose node runs its own root model
    # and then dispatches a nested child model. Both models carry instance-level
    # role tags (what get_role_llm sets). The child's call must be attributed to
    # the child's role, not swallowed by the ambient parent tag -- which is what
    # _role_from_tags' prefer-the-LAST-tag rule exists for. Driven through real
    # LangGraph machinery so the tag ORDER is observed, not assumed.
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    class _S(TypedDict):
        done: bool

    root = _fake_llm_with_usage("analysis_root_deepagent", 100, 50)
    child = _fake_llm_with_usage("analysis_dispatch", 20, 10)

    def _root_node(state: _S) -> _S:
        root.invoke("root question")
        child.invoke("child question")  # nested; config propagates implicitly
        return {"done": True}

    graph = StateGraph(_S)
    graph.add_node("root", _root_node)
    graph.add_edge(START, "root")
    graph.add_edge("root", END)

    cb = CostCallback()
    # .with_config on the compiled graph makes the root role an AMBIENT tag on
    # every nested call -- the exact condition under which naive first-tag
    # attribution would misattribute the child's call to the root.
    compiled = graph.compile().with_config(tags=["agent_role:analysis_root_deepagent"])
    compiled.invoke({"done": False}, config={"callbacks": [cb]})

    breakdown = cb.breakdown()
    assert set(breakdown) == {"analysis_root_deepagent", "analysis_dispatch"}
    assert breakdown["analysis_dispatch"]["prompt_tokens"] == 20
    assert breakdown["analysis_dispatch"]["call_count"] == 1
    assert breakdown["analysis_root_deepagent"]["prompt_tokens"] == 100
