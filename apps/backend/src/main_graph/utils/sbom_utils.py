"""Helpers for extracting data from CycloneDX SBOM structures."""

from __future__ import annotations

import re


def get_vcs_url(sbom: dict, dep_name: str) -> str | None:
    """Return the VCS URL for a component from its externalReferences, or None."""
    for component in sbom.get("components", []):
        if component.get("name") == dep_name:
            for ref in component.get("externalReferences", []):
                if ref.get("type") == "vcs":
                    return ref.get("url")
    return None


def get_component_version(sbom: dict, dep_name: str) -> str | None:
    """Return the installed version of a component from the SBOM, or None."""
    for component in sbom.get("components", []):
        if component.get("name") == dep_name:
            return component.get("version")
    return None


def parse_github_owner_repo(url: str) -> tuple[str, str] | None:
    """Parse a GitHub URL into (owner, repo). Returns None if not a GitHub URL."""
    match = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", url)
    if match:
        return match.group(1), match.group(2)
    return None
