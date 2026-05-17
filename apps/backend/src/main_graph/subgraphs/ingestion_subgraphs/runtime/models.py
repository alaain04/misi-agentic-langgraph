from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CompatibilityIssue(BaseModel):
    package: str
    required: str
    actual: str | None = None
    description: str | None = None


class TestResult(BaseModel):
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class LintResult(BaseModel):
    errors: int = 0
    warnings: int = 0
    fixable: int = 0


class RuntimeEntry(BaseModel):
    node_version: str | None = None
    compatibility_issues: list[CompatibilityIssue] = Field(default_factory=list)
    test_results: TestResult | None = None
    lint_results: LintResult | None = None


class RuntimeCacheEntry(BaseModel):
    """One document per (package_name, package_version) in `runtime_cache`."""

    package_name: str
    package_version: str
    fetched_at: datetime
    entry: RuntimeEntry
