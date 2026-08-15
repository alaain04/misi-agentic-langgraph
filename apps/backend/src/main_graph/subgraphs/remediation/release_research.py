"""Agentic release-note research for remediation: for every non-r3 target
select_targets_node produces, iterates paginated GitHub release notes and
any linked migration docs to produce the ReleaseDigest the migration
planner reads. See docs/superpowers/specs/2026-08-15-remediation-release-
research-agent-design.md (D-RESEARCH, D-TOOLS)."""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx
from langchain_core.tools import tool

from src.utils.config import settings

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
_GH_TOKEN_HOSTS = {"github.com", "raw.githubusercontent.com"}
_MAX_REDIRECTS = 3
_TIMEOUT = 10.0
_DOC_CHAR_CAP = 2000


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
