#!/usr/bin/env python3
"""One-shot tool to create the E2E fixture corpus's GitHub repos.

Not part of the ongoing test harness — run once to create the repos (or
again, rarely, to recreate one that was lost). This script owns each
fixture's build spec (dependencies, package manager) so those never need to
be duplicated in tests/e2e/corpus.yaml, which only tracks repo_url + ground-
truth expectations. See docs/e2e-test-catalog.md Group 8 for what each
fixture encodes and why.

This only creates the private repos — it does NOT enable the backend to
clone them. clone_repo does anonymous `git clone` with no auth support
today; that's Workstream D1.

Usage:
    uv run python scripts/provision_fixtures.py [--dry-run] [--owner alaain04]

Exit codes:
    0  all fixtures created/verified
    1  a gh or git command failed
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# Build spec per fixture: name, package manager, and dependencies. See
# docs/e2e-test-catalog.md Group 8 for the rationale behind each choice.
# "-transitive-fanout" has install=False so the pushed repo genuinely has
# no committed lockfile, exercising install_deps's --package-lock-only
# fallback and the A3 no-lockfile cache-bypass gate on a live run.
FIXTURES: list[dict] = [
    {
        "name": "misi-e2e-validation-cve-direct",
        "package_manager": "npm",
        "install": True,
        "dependencies": {"lodash": "4.17.11"},
    },
    {
        "name": "misi-e2e-validation-cve-transitive",
        "package_manager": "pnpm",
        "install": True,
        "dependencies": {"mkdirp": "0.5.1"},
    },
    {
        "name": "misi-e2e-validation-gpl-license",
        "package_manager": "yarn",
        "install": True,
        "dependencies": {"ffmpeg-static": "5.3.0"},
    },
    # NOTE: yarn fixtures use plain Yarn Classic (the globally available
    # `yarn`, currently 1.x). Yarn Berry defaults to Plug'n'Play mode (no
    # node_modules, a vendored release binary, .pnp.cjs) which adds real
    # install-flow risk for no benefit this corpus needs — PM coverage here
    # only needs one yarn variant, not both.
    {
        "name": "misi-e2e-validation-unmaintained",
        "package_manager": "npm",
        "install": True,
        "dependencies": {"matcha": "0.7.0"},
    },
    {
        "name": "misi-e2e-validation-supply-chain",
        "package_manager": "npm",
        "install": True,
        "dependencies": {"uuidv4": "^6.2.13"},
    },
    {
        "name": "misi-e2e-validation-clean",
        "package_manager": "yarn",
        "install": True,
        "dependencies": {"date-fns": "^3.6.0", "nanoid": "^5.0.0"},
    },
    {
        "name": "misi-e2e-validation-transitive-fanout",
        "package_manager": "npm",
        "install": False,
        "dependencies": {"mkdirp": "0.5.1", "optimist": "0.6.1"},
    },
    {
        "name": "misi-e2e-validation-false-positive-trap",
        "package_manager": "pnpm",
        "install": True,
        "dependencies": {"lodash.merge": "^4.6.2"},
    },
]

# npm/pnpm generate ONLY the lockfile (verified: no node_modules created).
# Yarn Classic has no such flag — plain `yarn install` also materializes
# node_modules, so _push stages only package.json + the lockfile explicitly
# rather than committing whatever `install` happened to produce.
_INSTALL_CMD = {
    "npm": ["npm", "install", "--package-lock-only"],
    "pnpm": ["pnpm", "install", "--lockfile-only"],
    "yarn": ["yarn", "install"],
}

_LOCKFILE_NAME = {
    "npm": "package-lock.json",
    "pnpm": "pnpm-lock.yaml",
    "yarn": "yarn.lock",
}


def _run(
    cmd: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}" + (f"  (in {cwd})" if cwd else ""))
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def _repo_exists(owner: str, name: str) -> bool:
    result = _run(["gh", "repo", "view", f"{owner}/{name}"], check=False)
    return result.returncode == 0


def _materialize(fixture: dict, workdir: Path) -> None:
    package_json = {
        "name": fixture["name"],
        "version": "1.0.0",
        "private": True,
        "license": "MIT",
        "dependencies": fixture["dependencies"],
    }
    (workdir / "package.json").write_text(json.dumps(package_json, indent=2) + "\n")
    if fixture["install"]:
        pm = fixture["package_manager"]
        _run(_INSTALL_CMD[pm], cwd=workdir)


def _push(owner: str, fixture: dict, workdir: Path, dry_run: bool) -> str:
    name = fixture["name"]
    if dry_run:
        return "DRY-RUN"

    files_to_add = ["package.json"]
    if fixture["install"]:
        files_to_add.append(_LOCKFILE_NAME[fixture["package_manager"]])

    _run(["git", "-C", str(workdir), "init", "-q"])
    _run(["git", "-C", str(workdir), "add", *files_to_add])
    _run(
        [
            "git",
            "-C",
            str(workdir),
            "commit",
            "-q",
            "-m",
            f"fixture: {name}",
        ]
    )
    _run(["git", "-C", str(workdir), "branch", "-M", "main"])

    if not _repo_exists(owner, name):
        _run(
            [
                "gh",
                "repo",
                "create",
                f"{owner}/{name}",
                "--private",
                "--disable-issues",
                "--disable-wiki",
            ]
        )
        status = "CREATED"
    else:
        status = "EXISTS"

    remote_url = f"https://github.com/{owner}/{name}.git"
    _run(["git", "-C", str(workdir), "remote", "add", "origin", remote_url])
    _run(["git", "-C", str(workdir), "push", "-u", "-f", "origin", "main"])
    sha = _run(["git", "-C", str(workdir), "rev-parse", "HEAD"]).stdout.strip()
    return f"{status} PUSHED ({sha[:7]})"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision the E2E fixture corpus repos"
    )
    parser.add_argument("--owner", default="alaain04")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for fixture in FIXTURES:
        name = fixture["name"]
        print(f"\n=== {name} ===")
        if args.dry_run:
            print(f"  would create {args.owner}/{name} (private), "
                  f"push package.json (deps={fixture['dependencies']}, "
                  f"install={fixture['install']})")
            continue

        with tempfile.TemporaryDirectory(prefix=f"fixture-{name}-") as tmp:
            workdir = Path(tmp)
            _materialize(fixture, workdir)
            status = _push(args.owner, fixture, workdir, args.dry_run)
            print(f"  {status}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"FAIL: {' '.join(exc.cmd)} exited {exc.returncode}\n{exc.stderr}")
        sys.exit(1)
