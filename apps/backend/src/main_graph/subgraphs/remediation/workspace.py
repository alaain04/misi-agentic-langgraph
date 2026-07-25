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
    dst = tempfile.mkdtemp(prefix="remediation-")
    work = os.path.join(dst, "repo")
    shutil.copytree(src_repo_path, work, symlinks=True)
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
