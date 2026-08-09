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
