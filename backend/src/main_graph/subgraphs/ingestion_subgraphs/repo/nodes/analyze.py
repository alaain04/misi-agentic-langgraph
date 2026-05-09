"""Analyze node for the Repo subgraph.

Fetches GitHub data (commits, issues, releases, vulnerabilities) for the primary
package's repository, curates each entity type with a dual-phase approach
(deterministic pre-pass + LLM), then persists the result.

Repository coordinates are read from upstream_results["registry"] which the
registry subgraph populates with repository_owner / repository_name / repository_url.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from src.main_graph.subgraphs.ingestion_subgraphs.repo.dao import (
    repo_cache_dao,
    repo_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.models import (
    Commit,
    Issue,
    Release,
    RepoCacheEntry,
    RepoEntry,
    Repository,
    Vulnerability,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.curators.commits import (
    make_commit_curation_agent,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.curators.issues import (
    make_issue_curation_agent,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.curators.releases import (
    make_release_curation_agent,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.curators.vulnerabilities import (  # noqa: E501
    make_vulnerability_curation_agent,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.entity_fetch import (
    EntityFetcher,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.state import RepoState
from src.main_graph.subgraphs.ingestion_subgraphs.repo.tools.github_mcp_client import (
    GitHubMCPClient,
)
from src.utils.config import settings

_log = logging.getLogger(__name__)


def _since_iso(lookback_days: int) -> str:
    since = datetime.now(UTC) - timedelta(days=lookback_days)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


async def analyze(state: RepoState) -> dict:
    registry_data = state.get("upstream_results", {}).get("registry", {})
    owner = registry_data.get("repository_owner")
    name = registry_data.get("repository_name")
    url = registry_data.get("repository_url") or ""

    if not owner or not name:
        _log.warning("repo.analyze: no repository_owner/name in upstream registry data")
        result_id = await repo_dao.save(RepoEntry(repositories=[]))
        return {"result_id": result_id}

    # Cache check — skip MCP fetch and LLM curation if data is fresh
    cached = await repo_cache_dao.find_cached_entry(
        owner, name, settings.lookback_days, settings.repo_cache_max_age_days
    )
    if cached is not None:
        result_id = await repo_dao.save(cached.entry)
        _log.info(
            "repo.analyze: cache hit for %s/%s, result_id=%s", owner, name, result_id
        )
        return {"result_id": result_id}

    since = _since_iso(settings.lookback_days)
    until = _now_iso()
    batch_size = settings.reviewer_batch_size

    raw_commits: list[dict] = []
    raw_issues: list[dict] = []
    raw_releases: list[dict] = []
    raw_vulns: list[dict] = []

    try:
        async with GitHubMCPClient(pat=settings.github_token) as client:
            try:
                raw_commits = await EntityFetcher("commits", client).fetch(
                    owner, name, since, until
                )
            except Exception as exc:
                _log.warning("repo.analyze: fetch_commits failed: %s", exc)

            try:
                raw_issues = await EntityFetcher("issues", client).fetch(
                    owner, name, since
                )
            except Exception as exc:
                _log.warning("repo.analyze: fetch_issues failed: %s", exc)

            try:
                raw_releases = await EntityFetcher("releases", client).fetch(
                    owner, name, since
                )
            except Exception as exc:
                _log.warning("repo.analyze: fetch_releases failed: %s", exc)

            try:
                raw_vulns = await EntityFetcher("vulnerabilities", client).fetch(
                    owner, name, since
                )
            except Exception as exc:
                _log.warning("repo.analyze: fetch_vulnerabilities failed: %s", exc)
    except Exception as exc:
        _log.error("repo.analyze: GitHub client failed: %s", exc)
        result_id = await repo_dao.save(RepoEntry(repositories=[]))
        return {"result_id": result_id}

    # Curate each entity type
    try:
        curated_commits = await make_commit_curation_agent().curate(
            raw_commits, batch_size
        )
    except Exception as exc:
        _log.warning("repo.analyze: commit curation failed: %s", exc)
        curated_commits = raw_commits

    try:
        curated_issues = await make_issue_curation_agent().curate(
            raw_issues, batch_size
        )
    except Exception as exc:
        _log.warning("repo.analyze: issue curation failed: %s", exc)
        curated_issues = raw_issues

    try:
        curated_releases = await make_release_curation_agent().curate(
            raw_releases, batch_size
        )
    except Exception as exc:
        _log.warning("repo.analyze: release curation failed: %s", exc)
        curated_releases = raw_releases

    try:
        curated_vulns = await make_vulnerability_curation_agent().curate(
            raw_vulns, batch_size
        )
    except Exception as exc:
        _log.warning("repo.analyze: vuln curation failed: %s", exc)
        curated_vulns = raw_vulns

    # Map curated dicts to domain models
    commits = [
        Commit(
            sha=c.get("sha", ""),
            message=c.get("summary") or (c.get("message") or "")[:120],
            author=(c.get("commit") or {}).get("author", {}).get("name")
            if isinstance(c.get("commit"), dict)
            else None,
            commit_type=c.get("commit_type"),
            timestamp=c.get("timestamp"),
        )
        for c in curated_commits
        if c.get("sha")
    ]

    issues = [
        Issue(
            id=str(i.get("number", "")),
            title=i.get("standardized_title") or i.get("title", ""),
            state=i.get("state", "open"),
            created_at=i.get("created_at"),
            body=(i.get("body") or "")[:1000] or None,
            type=i.get("type"),
            summary=i.get("summary"),
            updated_at=i.get("updated_at"),
            closed_at=i.get("closed_at"),
            labels=[
                lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
                for lbl in (i.get("labels") or [])
            ],
        )
        for i in curated_issues
        if i.get("number") is not None
    ]

    releases = [
        Release(
            tag=r.get("tag_name") or str(r.get("id", "")),
            name=r.get("standardized_title") or r.get("name"),
            published_at=r.get("published_at"),
            body=(r.get("body") or "")[:2000] or None,
            release_type=r.get("release_type"),
            change_summary=r.get("change_summary"),
        )
        for r in curated_releases
    ]

    vulnerabilities = [
        Vulnerability(
            id=v.get("ghsa_id", ""),
            severity=v.get("severity_category", "unknown"),
            description=v.get("summary"),
            cve_id=v.get("cve_id"),
            affected_components=v.get("affected_components") or [],
            published_at=v.get("published_at"),
            cvss_score=_safe_float(
                (v.get("cvss") or {}).get("score")
                if isinstance(v.get("cvss"), dict)
                else v.get("cvss_score")
            ),
            cwe_ids=[
                c.get("cwe_id", "") if isinstance(c, dict) else str(c)
                for c in (v.get("cwes") or [])
            ],
        )
        for v in curated_vulns
        if v.get("ghsa_id")
    ]

    repository = Repository(
        url=url,
        owner=owner,
        name=name,
        commits=commits,
        issues=issues,
        releases=releases,
        vulnerabilities=vulnerabilities,
    )
    entry = RepoEntry(repositories=[repository])

    try:
        await repo_cache_dao.upsert_cached_entry(
            RepoCacheEntry(
                owner=owner,
                repo_name=name,
                lookback_days=settings.lookback_days,
                fetched_at=datetime.now(UTC),
                entry=entry,
            )
        )
    except Exception:
        _log.warning("repo.analyze: cache write failed for %s/%s", owner, name)

    result_id = await repo_dao.save(entry)
    _log.info(
        "repo.analyze: saved — commits=%d issues=%d releases=%d vulns=%d result_id=%s",
        len(commits),
        len(issues),
        len(releases),
        len(vulnerabilities),
        result_id,
    )
    return {"result_id": result_id}
