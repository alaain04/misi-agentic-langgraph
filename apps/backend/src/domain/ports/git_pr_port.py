from __future__ import annotations

from abc import ABC, abstractmethod


class GitPullRequestPort(ABC):
    @abstractmethod
    async def open_pr(self, work_dir: str, branch: str, title: str, body: str) -> str:
        """Create a branch, commit the working tree, push, open a PR.
        Returns the PR URL. Raises RuntimeError on any git/gh failure."""
