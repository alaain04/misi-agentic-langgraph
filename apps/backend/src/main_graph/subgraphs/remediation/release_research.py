"""Agentic release-note research for remediation: for every non-r3 target
select_targets_node produces, iterates paginated GitHub release notes and
any linked migration docs to produce the ReleaseDigest the migration
planner reads. See docs/superpowers/specs/2026-08-15-remediation-release-
research-agent-design.md (D-RESEARCH, D-TOOLS)."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import textwrap
import time
import uuid
from typing import cast
from urllib.parse import urlparse

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.changelog import fetch_release_notes_page
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.models.conductor import ToolCall, ToolResult
from src.models.remediation import ReleaseDigest, RemediationTarget, TargetInvestigation
from src.utils.config import settings
from src.utils.model_registry import AgentRole, get_role_llm

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
_GH_TOKEN_HOSTS = {"github.com", "raw.githubusercontent.com"}
_MAX_REDIRECTS = 3
_TIMEOUT = 10.0
_DOC_CHAR_CAP = 2000

_MAX_ITERATIONS = 4
_MAX_CONCURRENT_RESEARCH = 6
_llm = get_role_llm(AgentRole.REMEDIATION_RELEASE_RESEARCH)
_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_RESEARCH)

_RESEARCH_SYSTEM_PROMPT = textwrap.dedent("""\
    You are researching what changed for a Node.js dependency upgrade so a
    migration planner can decide what needs to change in consuming code.

    Target: {target_dep}
    Upgrading from {from_version} to {to_version}.

    Use get_release_notes to read the release notes in that version
    window. If a release body references a migration/upgrade guide
    document (e.g. "see MIGRATION.md", a linked upgrade guide), use
    fetch_doc to read it -- the actual guidance is often there, not in the
    release body itself. If get_release_notes reports has_more=true and
    you have not yet found breaking-change evidence, call it again with
    the next page.

    When you have enough evidence (or have exhausted what's available),
    finalize with:
    - migration_needed: true ONLY when the notes/guide describe a breaking
      change a typical consumer would have to adapt to. A pure bug/patch/
      feature release with no consumer-facing break sets this to false
      with an empty migration_guide -- do not write commentary explaining
      that nothing is needed.
    - breaking_changes: each concrete breaking change, as a separate item.
    - migration_guide: concrete guidance grounded in what you actually
      read, not generic advice.

    Never repeat a tool call with the same arguments. After {max_iter}
    iterations, finalize regardless of what you've found.
    """).strip()


class ReleaseResearchDecision(BaseModel):
    tool_calls: list[ToolCall]
    finalize: bool
    migration_needed: bool
    migration_guide: str = ""
    breaking_changes: list[str] = Field(default_factory=list)
    reasoning: str


def _format_tool_results(results: list[ToolResult]) -> str:
    if not results:
        return "No results yet."
    parts = []
    for tr in results:
        val = (
            f"ERROR: {tr.error}" if tr.error else json.dumps(tr.output, indent=2)[:1500]
        )
        parts.append(f"[{tr.tool}] -> {val}")
    return "\n\n".join(parts)


async def _run_research_tool(tc: ToolCall, tool_map: dict) -> ToolResult:
    start = time.monotonic()
    fn = tool_map.get(tc.tool)
    if fn is None:
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=tc.args,
            output={},
            error=f"unknown tool: {tc.tool}",
            duration_ms=0,
        )
    try:
        output = await fn.ainvoke(tc.args)
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=tc.args,
            output=output if isinstance(output, dict) else {"result": output},
            error=None,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as exc:
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=tc.args,
            output={},
            error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


async def _research_loop(
    target_dep: str,
    from_version: str | None,
    to_version: str | None,
    resolved_repo: tuple[str, str] | None,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
) -> ReleaseDigest:
    tools = [
        make_get_release_notes_tool(
            target_dep,
            from_version,
            to_version,
            resolved_repo,
            repo_path,
            container,
            docker_image,
        ),
        make_fetch_doc_tool(),
    ]
    tool_map = {t.name: t for t in tools}
    tool_results: list[ToolResult] = []
    system = _RESEARCH_SYSTEM_PROMPT.format(
        target_dep=target_dep,
        from_version=from_version or "unknown",
        to_version=to_version or "unknown",
        max_iter=_MAX_ITERATIONS,
    )
    structured = _llm.with_structured_output(
        ReleaseResearchDecision, method="function_calling"
    )

    try:
        decision: ReleaseResearchDecision | None = None
        for iteration in range(_MAX_ITERATIONS):
            last = iteration == _MAX_ITERATIONS - 1
            prompt = (
                f"Tool results so far:\n{_format_tool_results(tool_results)}\n\n"
                f"Iteration: {iteration + 1}/{_MAX_ITERATIONS}"
            )
            decision = cast(
                ReleaseResearchDecision,
                await structured.ainvoke(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ]
                ),
            )
            if decision.finalize or last:
                break
            if decision.tool_calls:
                results = await asyncio.gather(
                    *[_run_research_tool(tc, tool_map) for tc in decision.tool_calls]
                )
                tool_results.extend(results)
        assert decision is not None
        return ReleaseDigest(
            from_version=from_version,
            to_version=to_version,
            migration_needed=decision.migration_needed,
            migration_guide=decision.migration_guide,
            breaking_changes=decision.breaking_changes,
        )
    except Exception as exc:
        logger.warning(
            "_research_loop: research failed for %s: %s; defaulting to "
            "migration_needed=True (conservative)",
            target_dep,
            exc,
        )
        return ReleaseDigest(
            from_version=from_version,
            to_version=to_version,
            migration_needed=True,
            migration_guide="",
            breaking_changes=[f"research failed, assuming breaking: {exc}"],
        )


async def _research_bounded(
    target: RemediationTarget,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
) -> ReleaseDigest:
    async with _semaphore:
        return await _research_loop(
            target.target_dep,
            target.current_range,
            target.latest_version,
            target.resolved_repo,
            repo_path,
            container,
            docker_image,
        )


async def research_releases_node(
    state: RemediationState, config: RunnableConfig
) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])

    targets = state.get("targets") or {}
    investigations = state.get("investigations") or {}
    researchable = [
        RemediationTarget(**t) for t in targets.values() if t.get("tier") != "r3"
    ]
    if not researchable:
        return {"investigations": {}}

    digests = await asyncio.gather(
        *[
            _research_bounded(t, prep.repo_path, container, prep.docker_image)
            for t in researchable
        ]
    )

    updated: dict[str, dict] = {}
    for target, digest in zip(researchable, digests, strict=True):
        existing = investigations.get(target.target_dep)
        inv = (
            TargetInvestigation(**existing)
            if existing
            else TargetInvestigation(target_dep=target.target_dep, release=digest)
        )
        updated[target.target_dep] = inv.model_copy(
            update={"release": digest}
        ).model_dump()

    return {"investigations": updated}


def _resolve_public_ip(host: str) -> str | None:
    """Resolve host and return ONE globally-routable IP if every resolved
    address is public, else None. Returning (not just validating) the IP
    lets the caller connect directly to it -- pinning the connection to
    the address this function actually checked closes a DNS-rebinding gap
    a validate-then-separately-connect design would otherwise have: two
    independent DNS lookups let an attacker's nameserver answer each one
    differently (public for validation, private/metadata for the real
    connection).
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return None
    ips: set[str] = {str(info[4][0]) for info in infos}
    if not ips:
        return None
    for ip in ips:
        try:
            if not ipaddress.ip_address(ip).is_global:
                return None
        except ValueError:
            return None
    return next(iter(ips))


async def _fetch_doc_once(url: str) -> dict:
    """One hop: validate the URL, connect directly to its validated IP
    (not a second, independently-resolved hostname lookup -- see
    _resolve_public_ip), GET without following redirects. Returns either
    the terminal {"available": ...} result, or an internal
    {"_redirect": location} for the caller to re-validate and retry."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return {"available": False, "error": f"unsupported scheme: {parsed.scheme!r}"}
    if not parsed.hostname:
        return {"available": False, "error": "no host in URL"}
    resolved_ip = _resolve_public_ip(parsed.hostname)
    if resolved_ip is None:
        return {
            "available": False,
            "error": "URL host does not resolve to a public address",
        }

    headers = {"Host": parsed.hostname}
    if parsed.hostname in _GH_TOKEN_HOSTS and settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    pinned_netloc = (
        f"[{resolved_ip}]:{port}" if ":" in resolved_ip else f"{resolved_ip}:{port}"
    )
    pinned_url = parsed._replace(netloc=pinned_netloc).geturl()

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        async with client.stream(
            "GET",
            pinned_url,
            headers=headers,
            extensions={"sni_hostname": parsed.hostname},
        ) as r:
            if 300 <= r.status_code < 400 and r.headers.get("location"):
                return {"_redirect": r.headers["location"]}
            if r.status_code >= 400:
                return {"available": False, "error": f"HTTP {r.status_code}"}
            body = b""
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) >= _DOC_CHAR_CAP:
                    break
    return {
        "available": True,
        "url": url,
        "body": body[:_DOC_CHAR_CAP].decode(errors="replace"),
    }


async def fetch_doc(url: str) -> dict:
    """Fetch a URL a release body links to (MIGRATION.md, UPGRADING.md, an
    external guide). Hardened against SSRF: rejects non-http(s) schemes and
    any host that doesn't resolve to a public IP; GH_TOKEN is only attached
    when the validated host is exactly github.com or
    raw.githubusercontent.com. Redirects are never auto-followed -- each
    hop's target is re-validated the same way, up to 3 hops, so a redirect
    can't be used to reach a host the initial check would have rejected."""
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        try:
            result = await _fetch_doc_once(current)
        except Exception as exc:
            return {"available": False, "error": str(exc)}
        redirect = result.get("_redirect")
        if redirect is None:
            return result
        current = redirect
    return {"available": False, "error": "too many redirects"}


def make_fetch_doc_tool():
    @tool
    async def fetch_doc_tool(url: str) -> dict:
        """Fetch a document a release body links to (e.g. MIGRATION.md,
        UPGRADING.md, an external upgrade guide) when the release body
        itself just points at it instead of describing the change. Only
        public http(s) URLs are reachable."""
        return await fetch_doc(url)

    fetch_doc_tool.name = "fetch_doc"
    return fetch_doc_tool


def make_get_release_notes_tool(
    target_dep: str,
    from_version: str | None,
    to_version: str | None,
    resolved_repo: tuple[str, str] | None,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
):
    @tool
    async def get_release_notes(page: int = 1) -> dict:
        """Fetch one page of the target package's GitHub releases, windowed
        to the versions between the installed range and the target
        version. Returns has_more=True when an older, still-relevant
        release likely exists on the next page -- call again with the next
        page number if you need more evidence. Refuses page > 10."""
        if page > 10:
            return {"available": False, "error": "page limit (10) exceeded"}
        return await fetch_release_notes_page(
            target_dep,
            page,
            from_version,
            to_version,
            repo_path,
            container,
            docker_image,
            resolved_repo=resolved_repo,
        )

    return get_release_notes
