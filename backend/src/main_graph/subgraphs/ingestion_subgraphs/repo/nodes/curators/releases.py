"""Release curation agent — classifies GitHub releases.

Uses semver pre-pass for deterministic classification; LLM fallback for the rest.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.utils.llm import parse_llm_json

_log = logging.getLogger(__name__)

_ALLOWED_TYPES = frozenset({"major", "minor", "patch", "pre-release", "other"})
_PRE_RELEASE_PATTERNS = re.compile(
    r"[-.]?(alpha|beta|rc|preview|pre)[\d.]*", re.IGNORECASE
)
_SEMVER_FULL = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_SEMVER_MINOR = re.compile(r"^v?(\d+)\.(\d+)\.0$")
_SEMVER_MAJOR = re.compile(r"^v?(\d+)\.0\.0$")

_SYSTEM_PROMPT = """\
You are a GitHub release classifier. Given a list of releases, classify each one.
Return a JSON array where each element has EXACTLY these keys:
  "id" (string or int), "release_type" (string), "change_summary" (string),
  "standardized_title" (string)

Allowed values for "release_type": major, minor, patch, pre-release, other
- "change_summary": 1-3 sentences describing the release changes, no markdown
- "standardized_title": Title Case, max 80 characters

Return ONLY the JSON array, no other text.\
"""


def _semver_pre_pass(tag: str) -> str | None:
    if not tag:
        return None
    if _PRE_RELEASE_PATTERNS.search(tag):
        return "pre-release"
    if _SEMVER_MAJOR.match(tag):
        return "major"
    if _SEMVER_MINOR.match(tag):
        return "minor"
    if _SEMVER_FULL.match(tag):
        return "patch"
    return None


def _validate_item(item: dict, raw: dict) -> dict:
    result = {**raw, **item}
    if result.get("release_type") not in _ALLOWED_TYPES:
        result["release_type"] = "other"
    if not result.get("change_summary"):
        result["change_summary"] = "No release notes provided"
    if not result.get("standardized_title"):
        result["standardized_title"] = str(raw.get("tag_name") or raw.get("id", ""))
    return result


def _build_prompt(entities: list[dict]) -> str:
    lines = []
    for e in entities:
        tag = e.get("tag_name") or e.get("id", "")
        body = str(e.get("body") or "")[:300]
        lines.append(f"id={e.get('id')} tag={tag} notes={body}")
    return "Classify these GitHub releases:\n" + "\n".join(lines)


async def _call_llm(llm: Any, entities: list[dict]) -> list[dict]:
    raw_by_id = {str(e.get("id")): e for e in entities}
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_prompt(entities)},
    ]
    response = await llm.ainvoke(messages)
    curated = parse_llm_json(response.content or "")
    result = []
    returned_ids = set()
    for item in curated:
        rid = str(item.get("id", ""))
        raw = raw_by_id.get(rid, {})
        result.append(_validate_item(item, raw))
        returned_ids.add(rid)
    for e in entities:
        if str(e.get("id", "")) not in returned_ids:
            _log.warning("release_curation: LLM dropped id=%s", e.get("id"))
            result.append(
                {
                    **e,
                    "release_type": "other",
                    "change_summary": "No release notes provided",
                    "standardized_title": str(e.get("tag_name") or e.get("id", "")),
                }
            )
    return result


class ReleaseCurationAgent:
    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def curate(self, entities: list[dict], batch_size: int = 20) -> list[dict]:
        if not entities:
            return []
        pre_pass_results: list[dict] = []
        llm_entities: list[dict] = []
        for e in entities:
            rtype = _semver_pre_pass(e.get("tag_name") or "")
            if rtype is not None:
                pre_pass_results.append(
                    {
                        **e,
                        "release_type": rtype,
                        "change_summary": str(
                            e.get("body") or "No release notes provided"
                        )[:500]
                        or "No release notes provided",
                        "standardized_title": str(e.get("tag_name") or e.get("id", "")),
                    }
                )
            else:
                llm_entities.append(e)

        llm_results: list[dict] = []
        for i in range(0, len(llm_entities), batch_size):
            chunk = llm_entities[i : i + batch_size]
            try:
                llm_results.extend(await _call_llm(self._llm, chunk))
            except Exception as exc:
                _log.error("release_curation: chunk failed: %s", exc)
                for e in chunk:
                    llm_results.append(
                        {
                            **e,
                            "release_type": "other",
                            "change_summary": "No release notes provided",
                            "standardized_title": str(
                                e.get("tag_name") or e.get("id", "")
                            ),
                        }
                    )

        return pre_pass_results + llm_results


def make_release_curation_agent(llm: Any | None = None) -> ReleaseCurationAgent:
    if llm is None:
        from langchain_openai import ChatOpenAI

        from src.utils.config import settings

        llm = ChatOpenAI(model="gpt-4o-mini", api_key=settings.openai_api_key)
    return ReleaseCurationAgent(llm)
