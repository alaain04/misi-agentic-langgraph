from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Vulnerability(BaseModel):
    id: str
    severity: str = "unknown"
    description: str | None = None
    fixed_in: str | None = None
    cve_id: str | None = None
    affected_components: list[str] = Field(default_factory=list)
    published_at: str | None = None
    cvss_score: float | None = None
    cwe_ids: list[str] = Field(default_factory=list)


class Issue(BaseModel):
    id: str | int
    title: str
    state: str = "open"
    created_at: str | None = None
    body: str | None = None
    type: str | None = None
    summary: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None
    labels: list[str] = Field(default_factory=list)


class Release(BaseModel):
    tag: str
    name: str | None = None
    published_at: str | None = None
    body: str | None = None
    release_type: str | None = None
    change_summary: str | None = None


class Commit(BaseModel):
    sha: str
    message: str
    author: str | None = None
    commit_type: str | None = None
    timestamp: str | None = None


class Repository(BaseModel):
    url: str
    owner: str | None = None
    name: str | None = None
    stars: int | None = None
    open_issues: int | None = None
    vulnerabilities: list[Vulnerability] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    releases: list[Release] = Field(default_factory=list)
    commits: list[Commit] = Field(default_factory=list)


class RepoEntry(BaseModel):
    repositories: list[Repository] = Field(default_factory=list)


class RepoCacheEntry(BaseModel):
    """One document per (owner, repo_name, lookback_days) in `repo_cache` collection."""

    owner: str
    repo_name: str
    lookback_days: int
    fetched_at: datetime
    entry: RepoEntry
