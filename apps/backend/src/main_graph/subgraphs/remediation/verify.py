from __future__ import annotations

import json
import logging
import os

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.remediation.workspace import pm_commands
from src.models.remediation import VerificationResult

logger = logging.getLogger(__name__)

_PLACEHOLDER_TEST = 'echo "Error: no test specified" && exit 1'


def _scripts(work_dir: str) -> dict:
    try:
        with open(os.path.join(work_dir, "package.json")) as f:
            return json.load(f).get("scripts") or {}
    except Exception:
        return {}


def _audit_executable(package_manager: str) -> str:
    return package_manager if package_manager in ("pnpm", "yarn") else "npm"


def _resolved_from_audit(stdout: str, targeted_deps: list[str]) -> bool | None:
    try:
        data = json.loads(stdout)
    except Exception:
        return None
    vulnerable = set((data.get("vulnerabilities") or {}).keys())
    return not any(dep in vulnerable for dep in targeted_deps)


async def verify_working_copy(
    work_dir: str,
    container: ContainerRunPort,
    docker_image: str,
    package_manager: str,
    targeted_deps: list[str],
) -> VerificationResult:
    cmds = pm_commands(package_manager)
    volume = f"{work_dir}:/workspace"
    scripts = _scripts(work_dir)
    v = VerificationResult()

    async def _run(cmd: str) -> tuple[int, str, str]:
        return await container.run(
            image=docker_image,
            command=f"cd /workspace && {cmd}",
            volume=volume,
            run_as_root=True,
        )

    rc, _out, err = await _run(cmds["install"])
    if rc != 0:
        v.logs_snippet = err[:1000]
        return v
    v.installed = True

    if "build" in scripts:
        rc, _out, err = await _run(cmds["build"])
        v.built = rc == 0
        if rc != 0:
            v.logs_snippet = err[:1000]

    test_script = (scripts.get("test") or "").strip()
    if test_script and test_script != _PLACEHOLDER_TEST:
        rc, _out, err = await _run(cmds["test"])
        v.tested = rc == 0
        if rc != 0:
            v.logs_snippet = err[:1000]

    _rc, out, _err = await _run(f"{_audit_executable(package_manager)} audit --json")
    v.finding_resolved = _resolved_from_audit(out, targeted_deps)
    return v
