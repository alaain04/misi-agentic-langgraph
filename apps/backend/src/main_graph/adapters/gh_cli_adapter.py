from __future__ import annotations

import asyncio
import logging

from src.domain.ports.git_pr_port import GitPullRequestPort

logger = logging.getLogger(__name__)


class GhCliAdapter(GitPullRequestPort):
    async def _run(self, *args: str, cwd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(err.decode(errors="replace").strip() or f"{args[0]} failed")
        return out.decode(errors="replace")

    async def open_pr(
        self, work_dir: str, branch: str, title: str, body: str
    ) -> str:
        await self._run("git", "checkout", "-b", branch, cwd=work_dir)
        await self._run("git", "add", "-A", cwd=work_dir)
        await self._run(
            "git", "-c", "user.email=remediation@misi", "-c", "user.name=misi-remediation",
            "commit", "-m", title, cwd=work_dir,
        )
        await self._run("git", "push", "-u", "origin", branch, cwd=work_dir)
        out = await self._run(
            "gh", "pr", "create", "--title", title, "--body", body, "--head", branch,
            cwd=work_dir,
        )
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return lines[-1] if lines else ""
