"""Vulnerability curation agent — classifies GitHub security advisories."""

from __future__ import annotations

import logging
from typing import Any

from src.utils.llm import parse_llm_json

_log = logging.getLogger(__name__)

_ALLOWED_SEVERITIES = frozenset({"critical", "high", "medium", "low", "informational"})

_SYSTEM_PROMPT = """\
You are a security vulnerability classifier. Given a list of GitHub security advisories,
classify each one.
Return a JSON array where each element has EXACTLY these keys:
  "ghsa_id" (string), "severity_category" (string),
  "affected_components" (array of strings), "summary" (string)

Allowed values for "severity_category": critical, high, medium, low, informational
- "affected_components": list of affected package/component names (may be empty list)
- "summary": 1-3 sentences describing the vulnerability and its impact, no markdown

Return ONLY the JSON array, no other text.\
"""


def _validate_item(item: dict, raw: dict) -> dict:
    result = {**raw, **item}
    if result.get("severity_category") not in _ALLOWED_SEVERITIES:
        result["severity_category"] = "informational"
    if not isinstance(result.get("affected_components"), list):
        result["affected_components"] = []
    if not result.get("summary"):
        result["summary"] = str(raw.get("description") or "")[:200] or str(
            raw.get("ghsa_id") or ""
        )
    return result


def _build_prompt(entities: list[dict]) -> str:
    lines = []
    for e in entities:
        desc = str(e.get("description") or e.get("summary") or "")[:300]
        lines.append(
            f"ghsa_id={e.get('ghsa_id')} severity={e.get('severity', '')}"
            f" description={desc}"
        )
    return "Classify these GitHub security advisories:\n" + "\n".join(lines)


async def _call_llm(llm: Any, entities: list[dict]) -> list[dict]:
    raw_by_id = {e["ghsa_id"]: e for e in entities if "ghsa_id" in e}
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_prompt(entities)},
    ]
    response = await llm.ainvoke(messages)
    curated = parse_llm_json(response.content or "")
    result = []
    returned_ids = set()
    for item in curated:
        gid = item.get("ghsa_id")
        raw = raw_by_id.get(gid, {})
        result.append(_validate_item(item, raw))
        returned_ids.add(gid)
    for e in entities:
        if e.get("ghsa_id") not in returned_ids:
            _log.warning("vuln_curation: LLM dropped ghsa_id=%s", e.get("ghsa_id"))
            result.append(
                {
                    **e,
                    "severity_category": "informational",
                    "affected_components": [],
                    "summary": str(e.get("description") or e.get("ghsa_id", "")),
                }
            )
    return result


class VulnerabilityCurationAgent:
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
                _log.error("vuln_curation: chunk failed: %s", exc)
                for e in chunk:
                    results.append(
                        {
                            **e,
                            "severity_category": "informational",
                            "affected_components": [],
                            "summary": str(
                                e.get("description") or e.get("ghsa_id", "")
                            ),
                        }
                    )
        return results


def make_vulnerability_curation_agent(
    llm: Any | None = None,
) -> VulnerabilityCurationAgent:
    if llm is None:
        from langchain_openai import ChatOpenAI

        from src.utils.config import settings

        llm = ChatOpenAI(model="gpt-4o-mini", api_key=settings.openai_api_key)
    return VulnerabilityCurationAgent(llm)
