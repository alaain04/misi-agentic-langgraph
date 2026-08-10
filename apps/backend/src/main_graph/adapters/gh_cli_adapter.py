from __future__ import annotations

import asyncio
import logging

from src.domain.ports.git_pr_port import GitPullRequestPort

logger = logging.getLogger(__name__)

# Paths that must never enter a remediation commit, no matter how they ended
# up in work_dir (a fresh copy_repo excludes them, but a verification step
# that installs/builds inside this same work_dir before commit can put them
# right back -- this git-add boundary is the actual guarantee, not copy_repo).
# The glob variant catches nested occurrences too (e.g. npm workspaces).
_COMMIT_EXCLUDE_PATHSPECS = [
    ":(exclude)node_modules/",
    ":(exclude,glob)**/node_modules/",
    ":(exclude).codegraph/",
    ":(exclude,glob)**/.codegraph/",
]


class GhCliAdapter(GitPullRequestPort):
    async def _run(self, *args: str, cwd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            msg = err.decode(errors="replace").strip() or f"{args[0]} failed"
            raise RuntimeError(msg)
        return out.decode(errors="replace")

    async def open_pr(self, work_dir: str, branch: str, title: str, body: str) -> str:
        await self._run("git", "checkout", "-b", branch, cwd=work_dir)
        await self._run(
            "git", "add", "-A", "--", ".", *_COMMIT_EXCLUDE_PATHSPECS, cwd=work_dir
        )
        await self._run(
            "git",
            "-c",
            "user.email=remediation@misi",
            "-c",
            "user.name=misi-remediation",
            "commit",
            "-m",
            title,
            cwd=work_dir,
        )
        await self._run("git", "push", "-u", "origin", branch, cwd=work_dir)
        out = await self._run(
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--head",
            branch,
            cwd=work_dir,
        )
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return lines[-1] if lines else ""
