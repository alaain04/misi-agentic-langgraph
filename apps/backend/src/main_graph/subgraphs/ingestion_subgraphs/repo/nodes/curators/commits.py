"""Commit curation agent — classifies and summarizes GitHub commits."""

from __future__ import annotations

import logging
from typing import Any

from src.utils.llm import parse_llm_json

_log = logging.getLogger(__name__)

_ALLOWED_TYPES = frozenset(
    {"feature", "fix", "refactor", "docs", "chore", "test", "other"}
)

_PREFIX_MAP = {
    "feature:": "feature",
    "feat:": "feature",
    "fix:": "fix",
    "bugfix:": "fix",
    "hotfix:": "fix",
    "refactor:": "refactor",
    "docs:": "docs",
    "doc:": "docs",
    "chore:": "chore",
    "build:": "chore",
    "ci:": "chore",
    "release:": "chore",
    "test:": "test",
    "tests:": "test",
}

_SYSTEM_PROMPT = """\
You are a Git commit classifier. Given a list of commit messages, classify each one.
Return a JSON array where each element has EXACTLY these keys:
  "sha" (string), "commit_type" (string), "summary" (string)

Allowed values:
- "commit_type": feature, fix, refactor, docs, chore, test, other
- "summary": 1 sentence, max 120 characters, plain English, no markdown

Return ONLY the JSON array, no other text.\
"""


def _extract_timestamp(e: dict) -> str | None:
    ts = e.get("timestamp")
    if ts:
        return ts
    return e.get("commit", {}).get("author", {}).get("date")


def _pre_pass(message: str) -> str | None:
    lower = message.strip().lower()
    for prefix, ctype in _PREFIX_MAP.items():
        if lower.startswith(prefix):
            return ctype
    return None


def _validate_item(item: dict, raw: dict) -> dict:
    result = {**raw, **item}
    if result.get("commit_type") not in _ALLOWED_TYPES:
        result["commit_type"] = "other"
    if not result.get("summary"):
        result["summary"] = (raw.get("message") or "")[:80].split("\n")[0]
    if "timestamp" not in result:
        result["timestamp"] = _extract_timestamp(raw)
    return result


def _build_prompt(entities: list[dict]) -> str:
    lines = []
    for e in entities:
        msg = (e.get("message") or "")[:300]
        lines.append(f"sha={e.get('sha')} message={msg}")
    return "Classify these Git commits:\n" + "\n".join(lines)


async def _call_llm(llm: Any, entities: list[dict]) -> list[dict]:
    raw_by_sha = {e["sha"]: e for e in entities if "sha" in e}
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_prompt(entities)},
    ]
    response = await llm.ainvoke(messages)
    reviewed = parse_llm_json(response.content or "")
    result = []
    returned_shas = set()
    for item in reviewed:
        sha = item.get("sha")
        raw = raw_by_sha.get(sha, {})
        result.append(_validate_item(item, raw))
        returned_shas.add(sha)
    for e in entities:
        if e.get("sha") not in returned_shas:
            _log.warning("commit_curation: LLM dropped sha=%s", e.get("sha"))
            msg = (e.get("message") or "")[:80].split("\n")[0]
            result.append(
                {
                    **e,
                    "commit_type": "other",
                    "summary": msg,
                    "timestamp": _extract_timestamp(e),
                }
            )
    return result


class CommitCurationAgent:
    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def curate(self, entities: list[dict], batch_size: int = 20) -> list[dict]:
        if not entities:
            return []
        pre_pass_results: list[dict] = []
        llm_entities: list[dict] = []
        for e in entities:
            ctype = _pre_pass(e.get("message") or "")
            if ctype is not None:
                msg = (e.get("message") or "").split("\n")[0]
                pre_pass_results.append(
                    {
                        **e,
                        "commit_type": ctype,
                        "summary": msg[:120],
                        "timestamp": _extract_timestamp(e),
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
                _log.error("commit_curation: chunk failed: %s", exc)
                for e in chunk:
                    msg = (e.get("message") or "")[:80].split("\n")[0]
                    llm_results.append(
                        {
                            **e,
                            "commit_type": "other",
                            "summary": msg,
                            "timestamp": _extract_timestamp(e),
                        }
                    )

        order = {e.get("sha"): idx for idx, e in enumerate(entities)}
        all_results = pre_pass_results + llm_results
        all_results.sort(key=lambda r: order.get(r.get("sha"), 9999))
        return all_results


def make_commit_curation_agent(llm: Any | None = None) -> CommitCurationAgent:
    if llm is None:
        from langchain_openai import ChatOpenAI

        from src.utils.config import settings

        llm = ChatOpenAI(model="gpt-4o-mini", api_key=settings.openai_api_key)
    return CommitCurationAgent(llm)
