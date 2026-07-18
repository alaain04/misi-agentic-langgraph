"""Collects each dependency's raw license string.

npm's package-lock.json carries a "license" field per installed package,
mirrored from that package's own package.json, without needing an install
(see spec: 179/377 packages had the field in a sample lockfile). yarn.lock
and pnpm-lock.yaml don't carry this metadata, so those — plus any npm entry
missing the field — fall back to the npm registry packument.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from src.main_graph.tools.external_api import _npm_metadata
from src.models.results import PrepResult
from src.utils.config import settings

logger = logging.getLogger(__name__)


def _read_npm_lockfile_licenses(repo_path: str) -> dict[str, str]:
    path = os.path.join(repo_path, "package-lock.json")
    try:
        with open(path) as f:
            lock = json.load(f)
    except Exception as exc:
        logger.warning("license_collector: failed to read package-lock.json: %s", exc)
        return {}
    licenses: dict[str, str] = {}
    for key, entry in (lock.get("packages") or {}).items():
        if key == "" or "node_modules/" not in key:
            continue
        name = key.rsplit("node_modules/", 1)[-1]
        lic = entry.get("license")
        if lic:
            licenses[name] = lic
    return licenses


async def _resolve_via_registry(keys: list[str]) -> dict[str, str]:
    sem = asyncio.Semaphore(settings.license_lookup_concurrency)

    async def fetch(key: str) -> tuple[str, str | None]:
        name = key.rsplit("@", 1)[0]
        async with sem:
            meta = await _npm_metadata(name)
        if "error" in meta:
            return key, None
        lic = meta.get("license")
        if isinstance(lic, dict):  # legacy {"type": "MIT"} shape
            lic = lic.get("type")
        return key, lic

    results = await asyncio.gather(*[fetch(k) for k in keys])
    return {key: lic for key, lic in results if lic}


async def collect_licenses(prep: PrepResult) -> dict[str, str]:
    """Return {"name@version": raw_license_string} for every package in
    prep.dependency_graph["packages"]. Unresolved packages map to "UNKNOWN"
    — never guessed."""
    packages = prep.dependency_graph.get("packages", {})
    if not packages:
        return {}

    licenses: dict[str, str] = {}
    if prep.detected_package_manager == "npm":
        lockfile_licenses = _read_npm_lockfile_licenses(prep.repo_path)
        missing = []
        for key in packages:
            name = key.rsplit("@", 1)[0]
            lic = lockfile_licenses.get(name)
            if lic:
                licenses[key] = lic
            else:
                missing.append(key)
    else:
        missing = list(packages.keys())

    if missing:
        licenses.update(await _resolve_via_registry(missing))
        for key in missing:
            licenses.setdefault(key, "UNKNOWN")

    return licenses
