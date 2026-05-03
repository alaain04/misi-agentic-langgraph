"""Tools for cloning npm package source and reading package.json."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from langchain_core.tools import tool


def _normalize_github_url(url: str) -> str | None:
    """Return a canonical https://github.com/owner/repo URL or None."""
    url = url.strip().rstrip("/")
    url = url.replace("git+", "").replace("git://", "https://")
    if url.endswith(".git"):
        url = url[:-4]
    if "github.com" not in url:
        return None
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    return url


def _do_clone(repository_url: str, version: str, dest_dir: str) -> str:
    normalized = _normalize_github_url(repository_url)
    if normalized is None:
        return json.dumps(
            {"success": False, "error": f"Not a GitHub URL: '{repository_url}'"}
        )

    for tag in [f"v{version}", version]:
        try:
            proc = subprocess.run(  # noqa: S603
                ["git", "clone", "--depth", "1", "--branch", tag, normalized, dest_dir],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode == 0:
                return json.dumps(
                    {
                        "success": True,
                        "cloned_dir": dest_dir,
                        "tag_used": tag,
                        "repository_url": normalized,
                    }
                )
        except subprocess.TimeoutExpired:
            if Path(dest_dir).exists():
                shutil.rmtree(dest_dir, ignore_errors=True)
            return json.dumps(
                {"success": False, "error": f"git clone timed out for {normalized}"}
            )
        except FileNotFoundError:
            return json.dumps(
                {"success": False, "error": "git executable not found on PATH"}
            )

    if Path(dest_dir).exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    return json.dumps(
        {
            "success": False,
            "error": f"No tag found for version {version} in {normalized}",
        }
    )


@tool
async def clone_github_repo(repository_url: str, version: str, dest_dir: str) -> str:
    """
    Shallow-clone a GitHub repository at the matching version tag into dest_dir.

    Tries v{version} first, then {version} as fallback.
    Returns JSON: {success, cloned_dir, tag_used, repository_url} or {success, error}.
    """
    return _do_clone(repository_url, version, dest_dir)


@tool
async def read_package_json(package_dir: str) -> str:
    """
    Read and parse package.json from an npm package directory.

    Returns JSON: {success, scripts} or {success, error}.
    """
    pkg_json_path = Path(package_dir) / "package.json"
    try:
        content = json.loads(pkg_json_path.read_text(encoding="utf-8"))
        scripts = content.get("scripts", {})
        return json.dumps({"success": True, "scripts": scripts})
    except FileNotFoundError:
        return json.dumps(
            {"success": False, "error": f"package.json not found in {package_dir}"}
        )
    except json.JSONDecodeError as exc:
        return json.dumps({"success": False, "error": f"Malformed package.json: {exc}"})
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})
