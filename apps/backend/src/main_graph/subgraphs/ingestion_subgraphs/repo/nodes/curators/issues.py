"""Issue curation agent — classifies and normalizes GitHub issues."""

from __future__ import annotations

import logging
from typing import Any

from src.utils.llm import parse_llm_json

_log = logging.getLogger(__name__)

_ALLOWED_TYPES = frozenset(
    {
        "bug",
        "vulnerability",
        "documentation",
        "improvement",
        "feature",
        "question",
        "other",
    }
)

_SYSTEM_PROMPT = """\
You are a GitHub issue classifier. Given a list of GitHub issues, classify each one.
Return a JSON array where each element has EXACTLY these keys:
  "number" (int), "type" (string), "summary" (string), "standardized_title" (string)

Allowed values for "type":
  bug, vulnerability, documentation, improvement, feature, question, other
- "summary": 1-3 sentences in plain English, no markdown
- "standardized_title": Title Case, max 80 characters

Return ONLY the JSON array, no other text.\
"""


def _validate_item(item: dict, raw: dict) -> dict:
    result = {**raw, **item}
    if result.get("type") not in _ALLOWED_TYPES:
        result["type"] = "other"
    if not result.get("summary"):
        result["summary"] = str(raw.get("title", ""))[:100]
    if not result.get("standardized_title"):
        result["standardized_title"] = str(raw.get("title", ""))
    return result


def _build_prompt(entities: list[dict]) -> str:
    lines = []
    for e in entities:
        lines.append(
            f"number={e.get('number')} title={e.get('title', '')} "
            f"body={str(e.get('body', ''))[:300]}"
        )
    return "Classify these GitHub issues:\n" + "\n".join(lines)


async def _call_llm(llm: Any, entities: list[dict]) -> list[dict]:
    raw_by_number = {e["number"]: e for e in entities if "number" in e}
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_prompt(entities)},
    ]
    response = await llm.ainvoke(messages)
    curated = parse_llm_json(response.content or "")
    result = []
    for item in curated:
        number = item.get("number")
        raw = raw_by_number.get(number, {})
        result.append(_validate_item(item, raw))
    returned_numbers = {r["number"] for r in result if "number" in r}
    for e in entities:
        if e.get("number") not in returned_numbers:
            _log.warning("issue_curation: LLM dropped number=%s", e.get("number"))
            result.append(
                {
                    **e,
                    "type": "other",
                    "summary": str(e.get("title", ""))[:100],
                    "standardized_title": str(e.get("title", "")),
                }
            )
    return result


class IssueCurationAgent:
    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def curate(self, entities: list[dict], batch_size: int = 20) -> list[dict]:
        if not entities:
            return []
        results: list[dict] = []
        for i in range(0, len(entities), batch_size):
            chunk = entities[i : i + batch_size]
            try:
                results.extend(await _call_llm(self._llm, chunk))
            except Exception as exc:
                _log.error("issue_curation: chunk failed: %s", exc)
                for e in chunk:
                    results.append(
                        {
                            **e,
                            "type": "other",
                            "summary": str(e.get("title", ""))[:100],
                            "standardized_title": str(e.get("title", "")),
                        }
                    )
        return results


def make_issue_curation_agent(llm: Any | None = None) -> IssueCurationAgent:
    if llm is None:
        from langchain_openai import ChatOpenAI

        from src.utils.config import settings

        llm = ChatOpenAI(model="gpt-4o-mini", api_key=settings.openai_api_key)
    return IssueCurationAgent(llm)
