from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Vulnerability(BaseModel):
    id: str
    severity: str = "unknown"
    description: str | None = None
    fixed_in: str | None = None


class NpmAuthor(BaseModel):
    name: str | None = None
    email: str | None = None
    url: str | None = None


class NpmDist(BaseModel):
    tarball: str | None = None
    shasum: str | None = None
    integrity: str | None = None
    file_count: int | None = None
    unpacked_size: int | None = None


class PackageRecord(BaseModel):
    # existing fields
    name: str
    current_version: str
    latest_version: str | None = None
    outdated: bool = False
    deprecated: bool = False
    vulnerabilities: list[Vulnerability] = Field(default_factory=list)
    # new fields — all optional
    deprecated_message: str | None = None
    description: str | None = None
    license: str | None = None
    homepage: str | None = None
    repository_url: str | None = None
    keywords: list[str] = Field(default_factory=list)
    monthly_downloads: int | None = None
    dependencies: dict[str, str] = Field(default_factory=dict)
    dev_dependencies: dict[str, str] = Field(default_factory=dict)
    peer_dependencies: dict[str, str] = Field(default_factory=dict)
    optional_dependencies: dict[str, str] = Field(default_factory=dict)
    engines: dict[str, str] = Field(default_factory=dict)
    dist: NpmDist | None = None
    author: NpmAuthor | None = None
    maintainers: list[NpmAuthor] = Field(default_factory=list)
    has_shrinkwrap: bool | None = None


class NpmPackageCache(BaseModel):
    """One document per package name in `npm_package_cache` collection."""

    name: str
    fetched_at: datetime
    # top-level package doc
    latest_version: str | None = None
    dist_tags: dict[str, str] = Field(default_factory=dict)
    description: str | None = None
    keywords: list[str] = Field(default_factory=list)
    license: str | None = None
    homepage: str | None = None
    repository_url: str | None = None
    repository_type: str | None = None
    bugs_url: str | None = None
    bugs_email: str | None = None
    readme: str | None = None
    readme_filename: str | None = None
    time_created: str | None = None
    time_modified: str | None = None
    # version-specific fields
    deprecated: bool = False
    deprecated_message: str | None = None
    main: str | None = None
    scripts: dict[str, str] = Field(default_factory=dict)
    engines: dict[str, str] = Field(default_factory=dict)
    author: NpmAuthor | None = None
    contributors: list[NpmAuthor] = Field(default_factory=list)
    maintainers: list[NpmAuthor] = Field(default_factory=list)
    dependencies: dict[str, str] = Field(default_factory=dict)
    dev_dependencies: dict[str, str] = Field(default_factory=dict)
    peer_dependencies: dict[str, str] = Field(default_factory=dict)
    optional_dependencies: dict[str, str] = Field(default_factory=dict)
    dist: NpmDist | None = None
    npm_user: str | None = None
    npm_version: str | None = None
    node_version: str | None = None
    has_shrinkwrap: bool | None = None
    monthly_downloads: int | None = None


class RegistryEntry(PackageRecord):
    """One document per dependency saved in the `registries` collection."""

    repository_owner: str | None = None
    repository_name: str | None = None
