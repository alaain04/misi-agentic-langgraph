from __future__ import annotations

import asyncio
import logging
import os
import shutil

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.remediation.verify import verify_working_copy
from src.main_graph.subgraphs.remediation.workspace import (
    apply_bump,
    copy_repo,
    replace_dependency,
)
from src.models.remediation import Remediation, VerificationResult

logger = logging.getLogger(__name__)


async def _git_apply(work_dir: str, patch: str) -> bool:
    if not patch.strip():
        return True
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        work_dir,
        "apply",
        "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate(input=patch.encode())
    if proc.returncode != 0:
        logger.warning("git apply failed: %s", err.decode(errors="replace")[:500])
        return False
    return True


async def apply_group_changes(work_dir: str, members: list[Remediation]) -> bool:
    """Deterministically replay a settled group's changes onto a working
    copy: structured bumps/replacements applied declaratively (never a raw
    patch for package.json, to avoid manifest merge conflicts), code
    changes (Tier 2/3) via `git apply` of each member's own diff. Returns
    False if any member's change fails to apply - the caller must not
    treat a partial apply as success."""
    ok = True
    for member in members:
        if (
            member.strategy == "replace"
            and member.replacement_dep
            and member.replacement_range
        ):
            if not replace_dependency(
                work_dir,
                member.target_dep,
                member.replacement_dep,
                member.replacement_range,
            ):
                ok = False
        elif member.to_range:
            if not apply_bump(work_dir, member.target_dep, member.to_range):
                ok = False
        if member.patch and not await _git_apply(work_dir, member.patch):
            ok = False
    return ok


async def replay_and_verify_group(
    members: list[Remediation],
    base_repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
    package_manager: str,
) -> VerificationResult:
    """The deterministic backstop (spec D6): replay a settled group's
    changes onto a fresh clean clone and re-run full verification from
    scratch. Never trusts any member's own self-reported status."""
    work_dir = copy_repo(base_repo_path)
    try:
        if not await apply_group_changes(work_dir, members):
            return VerificationResult(
                logs_snippet="one or more changes failed to apply cleanly"
            )
        targeted = sorted(
            {dep for m in members for dep in [m.target_dep, *m.addresses]}
        )
        return await verify_working_copy(
            work_dir, container, docker_image, package_manager, targeted
        )
    finally:
        shutil.rmtree(os.path.dirname(work_dir), ignore_errors=True)
