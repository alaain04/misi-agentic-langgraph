import asyncio

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from src.utils.cost import CostCallback


def _fake_llm_with_usage(role: str, prompt_tokens: int, completion_tokens: int):
    msg = AIMessage(
        content="ok",
        response_metadata={
            "token_usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
            "model_name": "gpt-5.4-mini-2026-03-17",
        },
    )
    return GenericFakeChatModel(messages=iter([msg])).with_config(
        tags=[f"agent_role:{role}"]
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


def test_nested_tags_prefer_last_role_tag_for_specificity():
    # Regression test: when a parent graph tag (inherited) and child tag (local)
    # are both present, LangChain puts the parent tag first and the child tag last.
    # _role_from_tags should return the LAST matching tag (most specific/innermost),
    # not the first. This ensures subagent calls are attributed to their own role,
    # not the root deep agent's role.
    # See: langchain_core.callbacks.manager._configure appends inheritable_tags
    # before local_tags.
    from uuid import uuid4

    from langchain_core.outputs import Generation, LLMResult

    cb = CostCallback()

    # Simulate nested call: parent (root agent) tag followed by child (subagent) tag.
    # This mirrors the real scenario where _deep_agent.with_config(
    #   tags=["agent_role:analysis_root_deepagent"]
    # ) causes that tag to be inherited, and then the subagent's
    # get_role_llm(AgentRole.ANALYSIS_DISPATCH) adds its own tag.
    parent_and_child_tags = [
        "agent_role:analysis_root_deepagent",
        "agent_role:analysis_dispatch",
    ]

    run_id = uuid4()
    cb._start_times[run_id] = None  # Prevent latency calc issues
    response = LLMResult(
        generations=[[Generation(text="ok")]],
        llm_output={
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
            },
            "model_name": "gpt-5.4-mini-2026-03-17",
        },
    )
    cb.on_llm_end(response=response, run_id=run_id, tags=parent_and_child_tags)

    # Should attribute to analysis_dispatch (the child/last tag), not the
    # inherited parent tag analysis_root_deepagent
    breakdown = cb.breakdown()
    assert set(breakdown) == {"analysis_dispatch"}
    assert breakdown["analysis_dispatch"]["prompt_tokens"] == 100
    assert breakdown["analysis_dispatch"]["completion_tokens"] == 50
