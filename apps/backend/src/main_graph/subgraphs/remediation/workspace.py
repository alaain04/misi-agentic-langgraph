from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile

_PM_COMMANDS: dict[str, dict[str, str]] = {
    "npm": {"install": "npm install", "build": "npm run build", "test": "npm test"},
    "pnpm": {
        "install": "pnpm install --no-frozen-lockfile",
        "build": "pnpm run build",
        "test": "pnpm test",
    },
    "yarn": {"install": "yarn install", "build": "yarn build", "test": "yarn test"},
}


def pm_commands(package_manager: str) -> dict[str, str]:
    return _PM_COMMANDS.get(package_manager, _PM_COMMANDS["npm"])


def copy_repo(src_repo_path: str) -> str:
    """Copy the repo into a fresh scratch dir under the project's own `tmp/`
    -- not the OS default tempdir (`tempfile.mkdtemp()`'s bare form lands
    under /var/folders or /tmp, which Docker Desktop's default file-sharing
    allowlist excludes on macOS; a docker run -v mount of that path silently
    sees an empty directory, so `npm install` in the container ENOENTs on
    package.json even though the host-side copy is intact). `tmp/` mirrors
    where clone_repo already puts its checkout, which containers can read."""
    base = os.path.abspath("tmp")
    os.makedirs(base, exist_ok=True)
    dst = tempfile.mkdtemp(prefix="remediation-", dir=base)
    work = os.path.join(dst, "repo")
    # .codegraph (discovery's blast-radius index) and node_modules (left on
    # disk by install_deps, which npm-installs into repo_path's bind mount)
    # are both host-side tooling byproducts, not part of the target repo --
    # excluded here so neither can ride along into a `git add -A` commit in
    # open_pr. verify_working_copy reinstalls node_modules fresh inside the
    # container regardless, so nothing is lost by not carrying it over.
    shutil.copytree(
        src_repo_path,
        work,
        symlinks=True,
        ignore=shutil.ignore_patterns(".codegraph", "node_modules"),
    )
    return work


def apply_bump(work_dir: str, target_dep: str, to_range: str) -> bool:
    pkg_path = os.path.join(work_dir, "package.json")
    with open(pkg_path) as f:
        pkg = json.load(f)
    for section in ("dependencies", "devDependencies"):
        if target_dep in (pkg.get(section) or {}):
            pkg[section][target_dep] = to_range
            with open(pkg_path, "w") as f:
                json.dump(pkg, f, indent=2)
                f.write("\n")
            return True
    return False


def replace_dependency(
    work_dir: str, old_dep: str, new_dep: str, new_range: str
) -> bool:
    pkg_path = os.path.join(work_dir, "package.json")
    with open(pkg_path) as f:
        pkg = json.load(f)
    for section in ("dependencies", "devDependencies"):
        bucket = pkg.get(section) or {}
        if old_dep in bucket:
            del bucket[old_dep]
            bucket[new_dep] = new_range
            with open(pkg_path, "w") as f:
                json.dump(pkg, f, indent=2)
                f.write("\n")
            return True
    return False


async def working_copy_diff(work_dir: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        work_dir,
        "diff",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    return out.decode(errors="replace")
