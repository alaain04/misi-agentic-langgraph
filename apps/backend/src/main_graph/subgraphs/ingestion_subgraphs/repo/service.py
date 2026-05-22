"""Repo analysis — pure business logic."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.db.connection import get_db
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.main_graph.subgraphs.ingestion_subgraphs.repo.dao import RepoCacheDAO
from src.main_graph.subgraphs.ingestion_subgraphs.repo.models import (
    Issue,
    Release,
    RepoCacheEntry,
    RepoEntry,
    Repository,
    Vulnerability,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.curators.issues import (
    make_issue_curation_agent,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.curators.releases import (
    make_release_curation_agent,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.curators.vulnerabilities import (
    make_vulnerability_curation_agent,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.state import RepoState
from src.main_graph.subgraphs.ingestion_subgraphs.sbom_utils import (
    get_vcs_url,
    parse_github_owner_repo,
)
from src.utils.config import settings
from src.utils.workers_client import ingest_and_wait

_log = logging.getLogger(__name__)


def _safe_float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


async def _read_workers_cache(collection: str, owner: str, repo: str) -> list[dict]:
    col = get_db()[collection]
    doc = await col.find_one({"name": f"{owner}/{repo}"})
    if doc is None:
        return []
    data = doc.get("items") or doc.get("data") or []
    return data if isinstance(data, list) else []


async def analyze_service(
    state: RepoState,
    dao: IngestionResultPort,
    cache_dao: RepoCacheDAO,
) -> dict:
    dep_name = state.get("dependency_name", "")
    sbom = state.get("sbom_cyclonedx", {})

    vcs_url = get_vcs_url(sbom, dep_name) if dep_name else None
    parsed = parse_github_owner_repo(vcs_url) if vcs_url else None

    if not parsed:
        _log.warning("repo: no GitHub VCS URL in SBOM for %s", dep_name)
        result_id = await dao.save(RepoEntry(repositories=[]))
        return {"result_id": result_id}

    owner, name = parsed
    url = vcs_url or ""

    cached = await cache_dao.find_cached_entry(owner, name, settings.lookback_days, settings.repo_cache_max_age_days)
    if cached is not None:
        result_id = await dao.save(cached.entry)
        _log.info("repo: cache hit for %s/%s, result_id=%s", owner, name, result_id)
        return {"result_id": result_id}

    try:
        await ingest_and_wait(entity_types=["github_issues", "github_releases", "github_advisories"], items=[f"{owner}/{name}"])
    except Exception as exc:
        _log.warning("repo: workers ingest failed for %s/%s: %s", owner, name, exc)
        result_id = await dao.save(RepoEntry(repositories=[]))
        return {"result_id": result_id}

    raw_issues = await _read_workers_cache("github_issues_cache", owner, name)
    raw_releases = await _read_workers_cache("github_releases_cache", owner, name)
    raw_vulns = await _read_workers_cache("github_advisories_cache", owner, name)

    batch_size = settings.reviewer_batch_size

    try:
        curated_issues = await make_issue_curation_agent().curate(raw_issues, batch_size)
    except Exception as exc:
        _log.warning("repo: issue curation failed: %s", exc)
        curated_issues = raw_issues

    try:
        curated_releases = await make_release_curation_agent().curate(raw_releases, batch_size)
    except Exception as exc:
        _log.warning("repo: release curation failed: %s", exc)
        curated_releases = raw_releases

    try:
        curated_vulns = await make_vulnerability_curation_agent().curate(raw_vulns, batch_size)
    except Exception as exc:
        _log.warning("repo: vuln curation failed: %s", exc)
        curated_vulns = raw_vulns

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
            labels=[lbl.get("name", "") if isinstance(lbl, dict) else str(lbl) for lbl in (i.get("labels") or [])],
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
            cvss_score=_safe_float((v.get("cvss") or {}).get("score") if isinstance(v.get("cvss"), dict) else v.get("cvss_score")),
            cwe_ids=[c.get("cwe_id", "") if isinstance(c, dict) else str(c) for c in (v.get("cwes") or [])],
        )
        for v in curated_vulns
        if v.get("ghsa_id")
    ]

    repository = Repository(url=url, owner=owner, name=name, issues=issues, releases=releases, vulnerabilities=vulnerabilities)
    entry = RepoEntry(repositories=[repository])

    try:
        await cache_dao.upsert_cached_entry(RepoCacheEntry(owner=owner, repo_name=name, lookback_days=settings.lookback_days, fetched_at=datetime.now(UTC), entry=entry))
    except Exception:
        _log.warning("repo: cache write failed for %s/%s", owner, name)

    result_id = await dao.save(entry)
    _log.info("repo: saved — issues=%d releases=%d vulns=%d result_id=%s", len(issues), len(releases), len(vulnerabilities), result_id)
    return {"result_id": result_id}
