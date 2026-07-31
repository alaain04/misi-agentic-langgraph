# Trivy Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `npm_audit` (VulnerabilityAgent), the license-source half of `collect_licenses` (LicenseAgent), and the three hand-rolled npm/pnpm/yarn lockfile parsers in `dependency_graph.py` with Trivy, run inside the existing `ContainerRunPort` sandbox — as three independent, concern-gated scans, not one bundled call.

**Architecture:** Three new functions in `trivy_cli.py` (`trivy_sbom_scan`, `trivy_vuln_scan`, `trivy_license_scan`), each its own `trivy fs` invocation. `trivy_sbom_scan` (`--format cyclonedx`, no `--scanners` flag) backs `dependency_graph.py`'s direct/transitive graph and runs unconditionally at discovery/prep time, same as today. `trivy_vuln_scan` and `trivy_license_scan` are called only from inside `VulnerabilityAgent`/`LicenseAgent`'s own `run()`, so they only execute when the deep agent actually dispatches that agent for the user's concern — bundling them into one multi-scanner call would break that selectivity. All three share one Docker image (`aquasec/trivy`, pinned) and one **host-mounted persistent cache volume** for Trivy's vulnerability DB, added as a new `cache_volume` parameter on `ContainerRunPort.run()` — verified empirically this drops repeat-scan time from ~12s (cold DB download) to ~0.3s (warm), which the codebase's prior Trivy integration (removed 2026-07-05, see Prior Art below) never actually solved.

**Tech Stack:** Python 3.12, `uv`, pytest + pytest-asyncio, Docker (`aquasec/trivy`), MongoDB/Motor via `InputCacheDAO`.

## Prior Art — read this before touching anything in this plan

Trivy was already fully implemented in this codebase once, from roughly May 2026 (`c3ef21d feat: add shared trivy runner utility`) through July 5, 2026: a `generate_sbom` discovery node, a shared `utils/trivy.py` runner, and per-domain Trivy scans for vulnerabilities/license_compliance/supply_chain, all built on a mandatory CycloneDX SBOM pipeline stage. It was removed as part of the ReAct-conductor rewrite (`badc6e5`) that replaced the old rigid pipeline with today's LLM tool-calling architecture. The stated reason, in `docs/superpowers/specs/2026-07-05-react-conductor-design.md:188,190`: the full SBOM/dependency graph was **"prohibitively large output"** to feed into the new LLM-driven investigation loop, so the team moved to lighter, individually-callable tools instead.

This plan does not repeat that mistake: the SBOM/`dependency_graph` produced here is **never** serialized into an LLM prompt or tool result. It stays exactly where today's `dependency_graph.py` output already lives — consumed by pure Python functions (`resolve_installed_versions`, `compute_missing_direct_deps`, `is_direct`, `direct_dependents`) on `PrepResult`, never shown to the deep agent. The vuln/license scans are gated on actual agent dispatch, matching the current architecture's per-concern dispatch model rather than the old mandatory whole-tree pipeline stage that was removed. If a future engineer is tempted to expose the raw CycloneDX document or the full `packages` dict to an LLM tool call, re-read the removed design spec first.

Also inherited and fixed here: the old `utils/trivy.py`'s `--cache-dir /tmp/trivy-cache` fix (commit `5c46091`) only solved a container write-permission error — that path was inside the ephemeral `docker run --rm` container, so it never stopped the ~100MB vulnerability DB from being re-downloaded on every single scan. This plan's `cache_volume` (Task 1) is a real fix, verified by direct measurement, not just a permission workaround.

## Global Constraints

- Python 3.12, `uv run` for all commands (never bare `python`/`pip`).
- `ruff check` and `mypy` must pass on every file touched before a task's commit.
- Every existing test that isn't explicitly deleted by a task in this plan must keep passing — run the full suite (`uv run pytest -q`) at the end of Group 4, not just the touched test files.
- No emoji in code, commit messages, or comments.
- Do not touch `SupplyChainAgent`, `MaintenanceAgent`, or `WebResearchAgent` — Trivy has no equivalent for typosquatting/metadata heuristics, maintenance/outdatedness signals, or open-ended research; those stay as-is.
- Do not touch `license_rules.py` / `license_data.py` (the C1/C2/C3 project-vs-dependency compatibility engine) — Trivy has no equivalent for this; only `collect_licenses`'s data *source* changes in Group 3.
- Do not expose `dependency_graph`, the raw CycloneDX document, or Trivy's raw vuln/license JSON to any LLM prompt or `_react_loop` tool result. See Prior Art above.
- Preserve the exact existing shape of `dependency_graph`: `{"direct": {name: version}, "packages": {"name@version": {"version": str, "dependencies": [child_key, ...]}}}`. `resolve_installed_versions`, `compute_missing_direct_deps`, `count_dependencies`, `is_direct`, `direct_dependents`, `dependents_of` must not need any changes.
- `resolve_transitive_parent` (npm_cli.py, used by `SupplyChainAgent`) is explicitly OUT of scope for this plan — it still shells out to a live `npm ls` regardless of package manager, which is a real pre-existing gap, but retiring it in favor of the now-reliable `dependency_graph`'s `is_direct`/`direct_dependents` requires extending `base_agent.py`'s generic tool-injection mechanism (`_INJECTED_PARAMS`, shared by all LLM-driven agents), which is a separate, higher-blast-radius change. Flag it as a follow-up; do not fold it into this plan.

---

## Task 1: `ContainerRunPort` persistent cache volume

**Files:**
- Modify: `apps/backend/src/domain/ports/container_run_port.py`
- Modify: `apps/backend/src/main_graph/adapters/docker_container_adapter.py`
- Test: `apps/backend/tests/unit/test_docker_container_adapter.py`

**Interfaces:**
- Produces: `ContainerRunPort.run(..., cache_volume: str | None = None)` — when set, mounted as an additional `-v` flag alongside the existing `volume`. Used by Task 3's `trivy_cli.py` to mount a durable host directory for Trivy's DB across separate `docker run --rm` invocations.

- [ ] **Step 1: Write the failing test**

Add to `apps/backend/tests/unit/test_docker_container_adapter.py`:

```python
@pytest.mark.asyncio
async def test_run_with_cache_volume_adds_second_v_flag():
    adapter = DockerContainerAdapter()
    with patch(
        "asyncio.create_subprocess_exec", return_value=_mock_proc()
    ) as mock_exec:
        await adapter.run(
            image="aquasec/trivy:0.71.2",
            command="fs /workspace",
            volume="/repo:/workspace",
            cache_volume="/host/trivy-cache:/root/.cache/trivy",
        )

    call_args = mock_exec.call_args.args
    assert "/repo:/workspace" in call_args
    assert "/host/trivy-cache:/root/.cache/trivy" in call_args
    assert call_args.count("-v") == 2


@pytest.mark.asyncio
async def test_run_without_cache_volume_unchanged_behavior():
    adapter = DockerContainerAdapter()
    with patch(
        "asyncio.create_subprocess_exec", return_value=_mock_proc()
    ) as mock_exec:
        await adapter.run(image="alpine/git", command="echo hi")

    call_args = mock_exec.call_args.args
    assert call_args.count("-v") == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_docker_container_adapter.py -v`
Expected: the two new tests FAIL with `TypeError: run() got an unexpected keyword argument 'cache_volume'`.

- [ ] **Step 3: Update the port and adapter**

In `apps/backend/src/domain/ports/container_run_port.py`, change the abstract method:

```python
class ContainerRunPort(ABC):
    @abstractmethod
    async def run(
        self,
        image: str,
        command: str,
        volume: str | None = None,
        run_as_root: bool = False,
        secret_env: dict[str, str] | None = None,
        cache_volume: str | None = None,
    ) -> tuple[int, str, str]:
        """Run a container. Returns (returncode, stdout, stderr).

        `secret_env` values are delivered via Docker's bare `-e VARNAME`
        form (name only, no `=value`) so they flow through process
        environment inheritance only — the value never appears in the
        constructed command list, which adapters log verbatim.

        `cache_volume` is a second `host:container` mount, independent of
        `volume`, for state that must persist across separate `docker run
        --rm` invocations (e.g. Trivy's vulnerability DB) — `volume` alone
        is wiped with the container on every call.
        """
        ...
```

In `apps/backend/src/main_graph/adapters/docker_container_adapter.py`:

```python
class DockerContainerAdapter(ContainerRunPort):
    async def run(
        self,
        image: str,
        command: str,
        volume: str | None = None,
        run_as_root: bool = False,
        secret_env: dict[str, str] | None = None,
        cache_volume: str | None = None,
    ) -> tuple[int, str, str]:
        cmd = ["docker", "run", "--rm"]
        if volume:
            cmd += ["-v", volume]
        if cache_volume:
            cmd += ["-v", cache_volume]
        if not run_as_root:
            cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
        if secret_env:
            for key in secret_env:
                cmd += ["-e", key]
        cmd += ["--entrypoint", "sh", image, "-c", command]
        logger.info("docker: %s", " ".join(cmd))

        subprocess_env = {**os.environ, **secret_env} if secret_env else None
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=subprocess_env,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=_TIMEOUT
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", f"timed out after {_TIMEOUT}s"

        assert proc.returncode is not None  # communicate() waits for exit
        return (
            proc.returncode,
            stdout_b.decode(errors="replace"),
            stderr_b.decode(errors="replace")[:3000],
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_docker_container_adapter.py -v`
Expected: all PASS (the two new tests plus the four pre-existing ones).

- [ ] **Step 5: Lint and typecheck**

Run: `cd apps/backend && uv run ruff check src/domain/ports/container_run_port.py src/main_graph/adapters/docker_container_adapter.py && uv run mypy src/domain/ports/container_run_port.py src/main_graph/adapters/docker_container_adapter.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd apps/backend
git add src/domain/ports/container_run_port.py src/main_graph/adapters/docker_container_adapter.py tests/unit/test_docker_container_adapter.py
git commit -m "feat: add persistent cache_volume mount to ContainerRunPort"
```

---

## Task 2: Trivy settings, `.env.example`, and startup check

**Files:**
- Modify: `apps/backend/src/utils/config.py`
- Modify: `apps/backend/.env.example`
- Modify: `apps/backend/src/main.py`
- Test: `apps/backend/tests/unit/test_main.py` (create if it doesn't exist — check first with `find apps/backend/tests -iname "test_main.py"`)

**Interfaces:**
- Produces: `settings.trivy_image: str`, `settings.trivy_cache_dir: str` — consumed by Task 3's `trivy_cli.py`.

- [ ] **Step 1: Add settings fields**

In `apps/backend/src/utils/config.py`, add after `codegraph_docker_image`:

```python
    # Trivy (vulnerability/license scanning + SBOM/dependency-graph generation).
    # trivy_cache_dir is a HOST directory, mounted into every trivy container
    # invocation as a persistent cache_volume so the ~100MB vulnerability DB
    # is downloaded once, not on every scan (see docs/superpowers/plans/
    # 2026-07-31-trivy-adoption.md, Prior Art).
    trivy_image: str
    trivy_cache_dir: str
```

- [ ] **Step 2: Add to `.env.example`**

In `apps/backend/.env.example`, after the `CODEGRAPH_DOCKER_IMAGE` line:

```
# Trivy (vulnerability/license scanning + SBOM/dependency-graph generation)
TRIVY_IMAGE=aquasec/trivy:0.71.2
TRIVY_CACHE_DIR=tmp/trivy-cache
```

Also add the same two lines with real values to `apps/backend/.env` (untracked, local dev file) so the app still boots locally — check its current content first with `cat apps/backend/.env` before editing, since it holds real secrets you must not overwrite.

- [ ] **Step 3: Write the failing startup-check test**

Check first whether `apps/backend/tests/unit/test_main.py` exists. If not, create it:

```python
from unittest.mock import AsyncMock, patch

import pytest

from src.main import lifespan


@pytest.mark.asyncio
async def test_lifespan_checks_trivy_image_runnable():
    app = AsyncMock()
    with (
        patch("src.main.get_client") as mock_get_client,
        patch("src.main.DockerContainerAdapter") as mock_adapter_cls,
    ):
        mock_get_client.return_value.admin.command = AsyncMock()
        mock_adapter = mock_adapter_cls.return_value
        mock_adapter.run = AsyncMock(side_effect=[(0, "", ""), (0, "Version: 0.71.2", "")])

        async with lifespan(app):
            pass

    calls = mock_adapter.run.call_args_list
    images_checked = [c.kwargs.get("image") for c in calls]
    assert "trivy --version" in [c.kwargs.get("command") for c in calls]
    assert any("trivy" in str(img) for img in images_checked)
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/test_main.py -v`
Expected: FAILS — only one `adapter.run` call happens today (codegraph check), so `side_effect` with 2 values combined with the assertion on "trivy --version" fails.

- [ ] **Step 5: Add the startup check**

In `apps/backend/src/main.py`, after the existing codegraph startup check and before `yield`:

```python
    rc, _, stderr = await DockerContainerAdapter().run(
        image=settings.trivy_image, command="trivy --version"
    )
    if rc != 0:
        raise RuntimeError(
            f"trivy image '{settings.trivy_image}' is not runnable (exit {rc}): {stderr}"
        )
    logger.info("startup check: trivy image runnable")
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/test_main.py -v`
Expected: PASS.

- [ ] **Step 7: Lint and typecheck**

Run: `cd apps/backend && uv run ruff check src/utils/config.py src/main.py tests/unit/test_main.py && uv run mypy src/utils/config.py src/main.py`
Expected: no errors. `mypy` will fail on missing `.env` values only at runtime, not at typecheck time — no action needed there.

- [ ] **Step 8: Commit**

```bash
cd apps/backend
git add src/utils/config.py .env.example src/main.py tests/unit/test_main.py
git commit -m "feat: add trivy_image/trivy_cache_dir settings and startup check"
```

---

## Task 3: `trivy_cli.py` — three independent scan functions

**Files:**
- Create: `apps/backend/src/main_graph/tools/trivy_cli.py`
- Test: `apps/backend/tests/unit/tools/test_trivy_cli.py`

**Interfaces:**
- Consumes: `ContainerRunPort.run(..., cache_volume=...)` from Task 1; `settings.trivy_image`, `settings.trivy_cache_dir` from Task 2.
- Produces: `trivy_sbom_scan(repo_path: str, container: ContainerRunPort) -> dict`, `trivy_vuln_scan(repo_path: str, container: ContainerRunPort) -> dict`, `trivy_license_scan(repo_path: str, container: ContainerRunPort) -> dict`. Each returns the raw parsed Trivy JSON on success, or `{"error": "..."}` on any execution failure (non-zero exit, empty/unparseable output, missing top-level schema key) — mirroring the fix already applied to `npm_audit` (see `bugfix_npm_audit_pnpm_silent_zero_findings` — never let an execution failure silently read downstream as "0 findings"). Consumed by Task 4/5 (vuln), Task 6/7 (license), Task 8 (SBOM).

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/tests/unit/tools/test_trivy_cli.py`:

```python
import json
from unittest.mock import AsyncMock

import pytest

from src.main_graph.tools.trivy_cli import (
    trivy_license_scan,
    trivy_sbom_scan,
    trivy_vuln_scan,
)


def _container(stdout: str = "", stderr: str = "", rc: int = 0) -> AsyncMock:
    container = AsyncMock()
    container.run.return_value = (rc, stdout, stderr)
    return container


@pytest.mark.asyncio
async def test_trivy_sbom_scan_runs_cyclonedx_no_scanners():
    container = _container(stdout=json.dumps({"bomFormat": "CycloneDX", "components": []}))
    await trivy_sbom_scan(repo_path="/tmp/repo", container=container)

    _, kwargs = container.run.call_args
    assert kwargs["command"] == "trivy fs --format cyclonedx /workspace"
    assert kwargs["volume"] == "/tmp/repo:/workspace"
    assert kwargs["cache_volume"] is not None
    assert kwargs["cache_volume"].endswith(":/root/.cache/trivy")


@pytest.mark.asyncio
async def test_trivy_sbom_scan_returns_document_on_success():
    doc = {"bomFormat": "CycloneDX", "specVersion": "1.7", "components": []}
    container = _container(stdout=json.dumps(doc))
    result = await trivy_sbom_scan(repo_path="/tmp/repo", container=container)
    assert result == doc


@pytest.mark.asyncio
async def test_trivy_sbom_scan_surfaces_error_when_binary_missing():
    container = _container(stdout="", stderr="sh: trivy: not found", rc=127)
    result = await trivy_sbom_scan(repo_path="/tmp/repo", container=container)
    assert "error" in result
    assert "trivy: not found" in result["error"]


@pytest.mark.asyncio
async def test_trivy_vuln_scan_runs_vuln_scanner_only():
    container = _container(stdout=json.dumps({"SchemaVersion": 2, "Results": []}))
    await trivy_vuln_scan(repo_path="/tmp/repo", container=container)

    _, kwargs = container.run.call_args
    assert kwargs["command"] == "trivy fs --format json --scanners vuln /workspace"


@pytest.mark.asyncio
async def test_trivy_vuln_scan_surfaces_error_on_unparseable_output():
    container = _container(stdout="not json", stderr="", rc=0)
    result = await trivy_vuln_scan(repo_path="/tmp/repo", container=container)
    assert "error" in result


@pytest.mark.asyncio
async def test_trivy_vuln_scan_accepts_empty_results_as_success():
    container = _container(stdout=json.dumps({"SchemaVersion": 2, "Results": []}))
    result = await trivy_vuln_scan(repo_path="/tmp/repo", container=container)
    assert "error" not in result
    assert result["Results"] == []


@pytest.mark.asyncio
async def test_trivy_license_scan_runs_license_scanner_only():
    container = _container(stdout=json.dumps({"SchemaVersion": 2, "Results": []}))
    await trivy_license_scan(repo_path="/tmp/repo", container=container)

    _, kwargs = container.run.call_args
    assert kwargs["command"] == "trivy fs --format json --scanners license /workspace"


@pytest.mark.asyncio
async def test_trivy_license_scan_surfaces_error_on_exec_failure():
    container = AsyncMock()
    container.run.side_effect = Exception("container failed")
    result = await trivy_license_scan(repo_path="/tmp/repo", container=container)
    assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/tools/test_trivy_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.main_graph.tools.trivy_cli'`.

- [ ] **Step 3: Write `trivy_cli.py`**

Create `apps/backend/src/main_graph/tools/trivy_cli.py`:

```python
"""Trivy tools executed inside a sandboxed container: SBOM/dependency-graph
generation, vulnerability scanning, and license scanning.

Each function is its own separate `trivy fs` invocation rather than one
combined `--scanners vuln,license` call — VulnerabilityAgent and LicenseAgent
are dispatched independently based on relevance to the user's concern (see
deepagent/coverage.WHOLE_TREE_AGENT_TYPES), so bundling their data sources
would force every job to pay for both scanners regardless of which agent(s)
actually ran. All three share one persistent cache_volume for Trivy's
vulnerability DB (~100MB) so it downloads once, not on every ephemeral
`docker run --rm` invocation — see docs/superpowers/plans/
2026-07-31-trivy-adoption.md.
"""

from __future__ import annotations

import json
import logging
import os

from src.domain.ports.container_run_port import ContainerRunPort
from src.utils.config import settings

logger = logging.getLogger(__name__)

_CACHE_VOLUME_TARGET = "/root/.cache/trivy"


def _safe_json(text: str) -> dict:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


async def _run_trivy(
    args: list[str], repo_path: str, container: ContainerRunPort
) -> tuple[int, str, str]:
    os.makedirs(settings.trivy_cache_dir, exist_ok=True)
    command = "trivy " + " ".join(args) + " /workspace"
    volume = f"{repo_path}:/workspace"
    cache_volume = f"{os.path.abspath(settings.trivy_cache_dir)}:{_CACHE_VOLUME_TARGET}"
    return await container.run(
        image=settings.trivy_image,
        command=command,
        volume=volume,
        run_as_root=True,
        cache_volume=cache_volume,
    )


async def _scan(
    args: list[str],
    repo_path: str,
    container: ContainerRunPort,
    success_key: str,
    scan_name: str,
) -> dict:
    try:
        rc, stdout, stderr = await _run_trivy(args, repo_path, container)
    except Exception as exc:
        logger.warning("%s failed: %s", scan_name, exc)
        return {"error": str(exc)}

    result = _safe_json(stdout)
    if success_key not in result:
        detail = stderr.strip() or stdout.strip() or f"exit code {rc}"
        logger.warning(
            "%s: no usable output (rc=%d): %s", scan_name, rc, detail[:300]
        )
        return {"error": f"{scan_name} failed: {detail[:500]}"}
    return result


async def trivy_sbom_scan(repo_path: str, container: ContainerRunPort) -> dict:
    """Runs `trivy fs --format cyclonedx` (no vuln/license scanners — see
    module docstring). Returns the CycloneDX document: components +
    dependency-graph edges, no vulnerability or license data."""
    return await _scan(
        ["fs", "--format", "cyclonedx"],
        repo_path,
        container,
        success_key="bomFormat",
        scan_name="trivy_sbom_scan",
    )


async def trivy_vuln_scan(repo_path: str, container: ContainerRunPort) -> dict:
    """Runs `trivy fs --format json --scanners vuln`. Returns the raw Trivy
    vulnerability report (Results[].Vulnerabilities[])."""
    return await _scan(
        ["fs", "--format", "json", "--scanners", "vuln"],
        repo_path,
        container,
        success_key="SchemaVersion",
        scan_name="trivy_vuln_scan",
    )


async def trivy_license_scan(repo_path: str, container: ContainerRunPort) -> dict:
    """Runs `trivy fs --format json --scanners license`. Returns the raw
    Trivy license report (Results[].Licenses[])."""
    return await _scan(
        ["fs", "--format", "json", "--scanners", "license"],
        repo_path,
        container,
        success_key="SchemaVersion",
        scan_name="trivy_license_scan",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/tools/test_trivy_cli.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and typecheck**

Run: `cd apps/backend && uv run ruff check src/main_graph/tools/trivy_cli.py tests/unit/tools/test_trivy_cli.py && uv run mypy src/main_graph/tools/trivy_cli.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd apps/backend
git add src/main_graph/tools/trivy_cli.py tests/unit/tools/test_trivy_cli.py
git commit -m "feat: add trivy_cli with sbom/vuln/license scan functions"
```

---

## Task 4: `trivy_vuln_parser.py`

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/analysis/agents/trivy_vuln_parser.py`
- Test: `apps/backend/tests/unit/test_trivy_vuln_parser.py`

**Interfaces:**
- Consumes: raw dict from `trivy_vuln_scan` (Task 3) — shape `{"Results": [{"Vulnerabilities": [{"VulnerabilityID", "PkgName", "InstalledVersion", "FixedVersion", "Severity" (uppercase LOW/MEDIUM/HIGH/CRITICAL), "Title", "Description", "PrimaryURL"}, ...]}]}`.
- Produces: `parse_trivy_vuln_findings(trivy_output: dict, min_severity: str = "high") -> list[FindingNote]`. Consumed by Task 5's `VulnerabilityAgent`.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/tests/unit/test_trivy_vuln_parser.py`:

```python
from src.main_graph.subgraphs.analysis.agents.trivy_vuln_parser import (
    parse_trivy_vuln_findings,
)


def _trivy_output(*vulns: dict) -> dict:
    return {"SchemaVersion": 2, "Results": [{"Target": "package-lock.json", "Vulnerabilities": list(vulns)}]}


def test_parses_high_and_above_by_default():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-2020-8203",
            "PkgName": "lodash",
            "InstalledVersion": "4.17.15",
            "FixedVersion": "4.17.19",
            "Severity": "HIGH",
            "Title": "prototype pollution",
            "Description": "details here",
            "PrimaryURL": "https://avd.aquasec.com/nvd/cve-2020-8203",
        },
        {
            "VulnerabilityID": "CVE-LOW-1",
            "PkgName": "some-pkg",
            "InstalledVersion": "1.0.0",
            "FixedVersion": "1.0.1",
            "Severity": "LOW",
            "Title": "minor issue",
            "Description": "minor",
            "PrimaryURL": "https://example.com",
        },
    )
    findings = parse_trivy_vuln_findings(output, min_severity="high")
    assert len(findings) == 1
    assert findings[0].dep_name == "lodash"
    assert findings[0].severity == "high"
    assert "CVE-2020-8203" in findings[0].evidence[0].log_snippet
    assert findings[0].evidence[0].url == "https://avd.aquasec.com/nvd/cve-2020-8203"


def test_maps_critical_and_sorts_most_severe_first():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-A",
            "PkgName": "pkg-a",
            "InstalledVersion": "1.0.0",
            "FixedVersion": "1.0.1",
            "Severity": "HIGH",
            "Title": "a",
            "Description": "a",
            "PrimaryURL": "",
        },
        {
            "VulnerabilityID": "CVE-B",
            "PkgName": "pkg-b",
            "InstalledVersion": "2.0.0",
            "FixedVersion": None,
            "Severity": "CRITICAL",
            "Title": "b",
            "Description": "b",
            "PrimaryURL": "",
        },
    )
    findings = parse_trivy_vuln_findings(output, min_severity="low")
    assert [f.severity for f in findings] == ["critical", "high"]
    assert "no fix available" in findings[0].description


def test_empty_results_returns_no_findings():
    assert parse_trivy_vuln_findings({"SchemaVersion": 2, "Results": []}) == []


def test_missing_results_key_returns_no_findings():
    assert parse_trivy_vuln_findings({"SchemaVersion": 2}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/test_trivy_vuln_parser.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `trivy_vuln_parser.py`**

Create `apps/backend/src/main_graph/subgraphs/analysis/agents/trivy_vuln_parser.py`:

```python
"""Deterministic extraction of findings from `trivy fs --scanners vuln
--format json` output (Results[].Vulnerabilities[])."""

from __future__ import annotations

from src.models.conductor import EvidenceRef, FindingNote

_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "UNKNOWN": "info",
}
_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity, 0)


def parse_trivy_vuln_findings(
    trivy_output: dict, min_severity: str = "high"
) -> list[FindingNote]:
    """Convert a trivy_vuln_scan output into findings at or above
    `min_severity`, most severe first."""
    threshold = _rank(min_severity)
    findings: list[FindingNote] = []
    for result in (trivy_output or {}).get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            severity = _SEVERITY_MAP.get(vuln.get("Severity", "UNKNOWN"), "info")
            if _rank(severity) < threshold:
                continue
            installed = vuln.get("InstalledVersion", "unknown")
            fixed = vuln.get("FixedVersion") or "no fix available"
            vuln_id = vuln.get("VulnerabilityID", "unknown")
            findings.append(
                FindingNote(
                    dep_name=vuln.get("PkgName", "unknown"),
                    severity=severity,
                    description=(
                        f"{vuln.get('Title') or vuln_id}. "
                        f"{vuln.get('Description', '')} "
                        f"Installed {installed}; fixed in {fixed}."
                    ),
                    evidence=[
                        EvidenceRef(
                            tool="trivy",
                            url=vuln.get("PrimaryURL") or None,
                            log_snippet=(
                                f"{vuln_id}: severity={vuln.get('Severity')}; "
                                f"installed={installed}; fixed={fixed}"
                            ),
                        )
                    ],
                )
            )
    findings.sort(key=lambda f: _rank(f.severity), reverse=True)
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/test_trivy_vuln_parser.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and typecheck**

Run: `cd apps/backend && uv run ruff check src/main_graph/subgraphs/analysis/agents/trivy_vuln_parser.py tests/unit/test_trivy_vuln_parser.py && uv run mypy src/main_graph/subgraphs/analysis/agents/trivy_vuln_parser.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd apps/backend
git add src/main_graph/subgraphs/analysis/agents/trivy_vuln_parser.py tests/unit/test_trivy_vuln_parser.py
git commit -m "feat: add trivy vulnerability finding parser"
```

---

## Task 5: Swap `VulnerabilityAgent` from `npm_audit` to `trivy_vuln_scan`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/agents/vulnerability_agent.py`
- Modify: `apps/backend/tests/unit/test_vulnerability_agent.py`

**Interfaces:**
- Consumes: `trivy_vuln_scan` (Task 3), `parse_trivy_vuln_findings` (Task 4).
- Produces: no change to `VulnerabilityAgent.run()`'s external signature — `AgentDispatch, PrepResult, ContainerRunPort, InputCacheDAO -> tuple[EvidenceBundle, list[str], int]` unchanged, so `subagent_wrapper.py` needs no changes.

- [ ] **Step 1: Update the existing tests first (TDD against the new behavior)**

Rewrite `apps/backend/tests/unit/test_vulnerability_agent.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.subgraphs.analysis.agents.vulnerability_agent import (
    VulnerabilityAgent,
)
from src.models.results import AgentDispatch, PrepResult


def _prep(**kw) -> PrepResult:
    defaults = dict(
        job_id="j1",
        repo_path="/tmp/r",
        project_metadata={},
        manifest_files=[],
        detected_package_manager="npm",
        dependency_graph={"direct": {}},
        discovery_summary="s",
        vector_store_id="",
        repo_url="https://github.com/x/y",
        commit_sha="sha1",
    )
    return PrepResult(**{**defaults, **kw})


def _dispatch() -> AgentDispatch:
    return AgentDispatch(
        domain="security",
        hypothesis="h",
        packages_to_focus=[],
        agent_type="vulnerability_agent",
    )


class _FakeCache:
    def __init__(self, initial=None):
        self.store = dict(initial or {})
        self.put_calls: list[str] = []

    async def get(self, key, max_age_seconds=None):
        return self.store.get(key)

    async def put(self, key, data):
        self.put_calls.append(key)
        self.store[key] = data


@pytest.mark.asyncio
async def test_vulnerability_agent_uses_cached_scan():
    from src.db.input_cache import cache_key
    from src.main_graph.subgraphs.analysis.agents import vulnerability_agent as va

    prep = _prep()
    key = cache_key(prep.repo_url, prep.commit_sha, "npm", "trivy_vuln")
    cache = _FakeCache({key: {"SchemaVersion": 2, "Results": []}})
    scan_mock = AsyncMock()

    with patch.object(va, "trivy_vuln_scan", scan_mock):
        bundle, tools, _ = await VulnerabilityAgent().run(
            _dispatch(), prep, container=AsyncMock(), cache=cache
        )

    scan_mock.assert_not_awaited()
    assert cache.put_calls == []
    assert bundle.findings == []


@pytest.mark.asyncio
async def test_vulnerability_agent_populates_cache_on_miss():
    from src.main_graph.subgraphs.analysis.agents import vulnerability_agent as va

    prep = _prep()
    cache = _FakeCache()
    scan_mock = AsyncMock(return_value={"SchemaVersion": 2, "Results": []})

    with patch.object(va, "trivy_vuln_scan", scan_mock):
        await VulnerabilityAgent().run(
            _dispatch(), prep, container=AsyncMock(), cache=cache
        )

    scan_mock.assert_awaited_once()
    assert len(cache.put_calls) == 1


@pytest.mark.asyncio
async def test_vulnerability_agent_no_cache_runs_scan_directly():
    from src.main_graph.subgraphs.analysis.agents import vulnerability_agent as va

    prep = _prep()
    scan_mock = AsyncMock(return_value={"SchemaVersion": 2, "Results": []})

    with patch.object(va, "trivy_vuln_scan", scan_mock):
        await VulnerabilityAgent().run(
            _dispatch(), prep, container=AsyncMock(), cache=None
        )

    scan_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_vulnerability_agent_surfaces_scan_error_as_low_confidence():
    from src.main_graph.subgraphs.analysis.agents import vulnerability_agent as va

    prep = _prep()
    scan_mock = AsyncMock(return_value={"error": "trivy: not found"})

    with patch.object(va, "trivy_vuln_scan", scan_mock):
        bundle, _, _ = await VulnerabilityAgent().run(
            _dispatch(), prep, container=AsyncMock(), cache=None
        )

    assert bundle.confidence == 0.3
    assert bundle.findings == []
    assert "trivy: not found" in bundle.summary
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_vulnerability_agent.py -v`
Expected: FAIL — `vulnerability_agent` module has no `trivy_vuln_scan` attribute to patch.

- [ ] **Step 3: Rewrite `vulnerability_agent.py`**

Replace the full contents of `apps/backend/src/main_graph/subgraphs/analysis/agents/vulnerability_agent.py`:

```python
from __future__ import annotations

import logging

from src.db.input_cache import InputCacheDAO, cache_key, get_or_compute
from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.analysis.agents.base_agent import BaseAgent
from src.main_graph.subgraphs.analysis.agents.trivy_vuln_parser import (
    parse_trivy_vuln_findings,
)
from src.main_graph.tools.trivy_cli import trivy_vuln_scan
from src.models.results import AgentDispatch, EvidenceBundle, PrepResult
from src.utils.config import settings

logger = logging.getLogger(__name__)

_SCAN_TTL_SECONDS = 7 * 24 * 3600  # advisories publish over time; re-scan weekly


class VulnerabilityAgent(BaseAgent):
    """Deterministic agent: one Trivy scan over the whole tree, no LLM
    reasoning loop. Trivy reads the lockfile directly (package-lock.json /
    pnpm-lock.yaml / yarn.lock) rather than shelling out to the project's own
    package manager, so there is nothing to sample or reason about — we
    extract every advisory at or above the configured severity.
    packages_to_focus is ignored.
    """

    agent_type = "vulnerability_agent"
    description = (
        "Scans the ENTIRE dependency tree for known CVEs and advisories via Trivy. "
        "Covers all direct and transitive packages in a single run, so "
        "packages_to_focus is ignored. Use when the concern "
        "involves security vulnerabilities, CVE IDs, or exploit risk."
    )
    system_prompt = ""  # unused: run() does not invoke the LLM

    def _agent_tools(self) -> list:
        return [trivy_vuln_scan]

    async def run(
        self,
        dispatch: AgentDispatch,
        prep: PrepResult,
        container: ContainerRunPort | None = None,
        cache: InputCacheDAO | None = None,
    ) -> tuple[EvidenceBundle, list[str], int]:
        async def _scan() -> dict:
            return await trivy_vuln_scan(repo_path=prep.repo_path, container=container)

        # Trivy scan is a deterministic container call for a fixed lockfile;
        # cache it by commit sha with a short TTL (advisories publish over
        # time). Miss or error recomputes.
        if cache is not None and prep.commit_sha:
            key = cache_key(
                prep.repo_url,
                prep.commit_sha,
                prep.detected_package_manager,
                "trivy_vuln",
            )
            output = await get_or_compute(cache, key, _scan, _SCAN_TTL_SECONDS)
        else:
            output = await _scan()
        min_severity = settings.vuln_min_severity
        error = output.get("error") if isinstance(output, dict) else None
        findings = [] if error else parse_trivy_vuln_findings(output, min_severity)

        if error:
            logger.warning("vulnerability_agent: scan failed: %s", error)
            summary = f"Dependency vulnerability scan failed: {error}"
        else:
            logger.info(
                "vulnerability_agent: scanned whole tree, %d finding(s) at "
                "severity>=%s",
                len(findings),
                min_severity,
            )
            summary = (
                f"Scanned the full dependency tree via Trivy. "
                f"{len(findings)} finding(s) at severity >= {min_severity}."
            )

        bundle = EvidenceBundle(
            domain=dispatch.domain,
            hypothesis=dispatch.hypothesis,
            packages_to_focus=[],
            findings=findings,
            summary=summary,
            confidence=0.3 if error else 1.0,
        )
        return bundle, ["trivy_vuln_scan"], 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_vulnerability_agent.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full analysis-subgraph and byte-stability suites**

Run: `cd apps/backend && uv run pytest tests/subgraphs/test_analysis_subgraph.py tests/subgraphs/test_whole_tree_agent_byte_stability.py -v`
Expected: PASS. If any test there directly imports or patches `npm_audit` on `vulnerability_agent`, update it to patch `trivy_vuln_scan` instead, following the same pattern as Step 1.

- [ ] **Step 6: Lint and typecheck**

Run: `cd apps/backend && uv run ruff check src/main_graph/subgraphs/analysis/agents/vulnerability_agent.py tests/unit/test_vulnerability_agent.py && uv run mypy src/main_graph/subgraphs/analysis/agents/vulnerability_agent.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
cd apps/backend
git add src/main_graph/subgraphs/analysis/agents/vulnerability_agent.py tests/unit/test_vulnerability_agent.py
git commit -m "feat: swap VulnerabilityAgent from npm_audit to trivy_vuln_scan"
```

---

## Task 6: `collect_licenses` reads from `trivy_license_scan`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/agents/license_collector.py`
- Modify: `apps/backend/tests/unit/test_license_collector.py`

**Interfaces:**
- Consumes: `trivy_license_scan` (Task 3); `prep.dependency_graph["packages"]` (unchanged shape).
- Produces: `collect_licenses(prep: PrepResult, container: ContainerRunPort) -> dict[str, str]` — same return shape as today (`{"name@version": raw_license_string}`, `"UNKNOWN"` for unresolved), new required `container` parameter. Consumed by Task 7's `LicenseAgent`.

- [ ] **Step 1: Rewrite the tests first**

Replace the full contents of `apps/backend/tests/unit/test_license_collector.py`:

```python
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from src.main_graph.subgraphs.analysis.agents.license_collector import collect_licenses
from src.models.results import PrepResult


def _prep(**kw) -> PrepResult:
    defaults = dict(
        job_id="j1",
        repo_path="/tmp/r",
        project_metadata={},
        manifest_files=[],
        detected_package_manager="npm",
        dependency_graph={"direct": {}, "packages": {}},
        discovery_summary="s",
        vector_store_id="",
    )
    return PrepResult(**{**defaults, **kw})


def _license_scan_output(*entries: dict) -> dict:
    return {
        "SchemaVersion": 2,
        "Results": [{"Target": "package-lock.json", "Licenses": list(entries)}],
    }


@pytest.mark.asyncio
async def test_collect_licenses_returns_empty_when_no_packages():
    prep = _prep(dependency_graph={"direct": {}, "packages": {}})
    result = await collect_licenses(prep, container=AsyncMock())
    assert result == {}


@pytest.mark.asyncio
async def test_collect_licenses_maps_scan_result_by_package_name():
    prep = _prep(
        dependency_graph={
            "direct": {"lodash": "4.17.15"},
            "packages": {"lodash@4.17.15": {"version": "4.17.15", "dependencies": []}},
        }
    )
    container = AsyncMock()
    container.run.return_value = (
        0,
        json.dumps(_license_scan_output({"PkgName": "lodash", "Name": "MIT"})),
        "",
    )
    result = await collect_licenses(prep, container=container)
    assert result == {"lodash@4.17.15": "MIT"}


@pytest.mark.asyncio
async def test_collect_licenses_marks_unresolved_packages_unknown():
    prep = _prep(
        dependency_graph={
            "direct": {"ghost": "1.0.0"},
            "packages": {"ghost@1.0.0": {"version": "1.0.0", "dependencies": []}},
        }
    )
    container = AsyncMock()
    container.run.return_value = (0, json.dumps(_license_scan_output()), "")
    result = await collect_licenses(prep, container=container)
    assert result == {"ghost@1.0.0": "UNKNOWN"}


@pytest.mark.asyncio
async def test_collect_licenses_applies_same_license_to_every_version():
    prep = _prep(
        dependency_graph={
            "direct": {},
            "packages": {
                "left-pad@1.0.0": {"version": "1.0.0", "dependencies": []},
                "left-pad@1.3.0": {"version": "1.3.0", "dependencies": []},
            },
        }
    )
    container = AsyncMock()
    container.run.return_value = (
        0,
        json.dumps(_license_scan_output({"PkgName": "left-pad", "Name": "MIT"})),
        "",
    )
    result = await collect_licenses(prep, container=container)
    assert result == {"left-pad@1.0.0": "MIT", "left-pad@1.3.0": "MIT"}


@pytest.mark.asyncio
async def test_collect_licenses_returns_unknown_on_scan_error():
    prep = _prep(
        dependency_graph={
            "direct": {"lodash": "4.17.15"},
            "packages": {"lodash@4.17.15": {"version": "4.17.15", "dependencies": []}},
        }
    )
    container = AsyncMock()
    container.run.return_value = (127, "", "sh: trivy: not found")
    result = await collect_licenses(prep, container=container)
    assert result == {"lodash@4.17.15": "UNKNOWN"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_license_collector.py -v`
Expected: FAIL — `collect_licenses` doesn't accept a `container` argument yet.

- [ ] **Step 3: Rewrite `license_collector.py`**

Replace the full contents of `apps/backend/src/main_graph/subgraphs/analysis/agents/license_collector.py`:

```python
"""Collects each dependency's raw license string via Trivy's license scanner.

Trivy reads the lockfile's own per-package license metadata uniformly across
npm/pnpm/yarn, so there is no per-package-manager fallback path needed (the
old implementation's npm-registry HTTP fallback for yarn/pnpm is gone —
Trivy covers all three from the lockfile alone). A scan failure degrades
every package to "UNKNOWN" rather than raising, matching how the rest of
this pipeline treats scan errors as low-confidence, not fatal.
"""

from __future__ import annotations

import logging

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.tools.trivy_cli import trivy_license_scan
from src.models.results import PrepResult

logger = logging.getLogger(__name__)


async def collect_licenses(prep: PrepResult, container: ContainerRunPort) -> dict[str, str]:
    """Return {"name@version": raw_license_string} for every package in
    prep.dependency_graph["packages"]. Unresolved packages (including a
    total scan failure) map to "UNKNOWN" — never guessed."""
    packages = prep.dependency_graph.get("packages", {})
    if not packages:
        return {}

    output = await trivy_license_scan(repo_path=prep.repo_path, container=container)
    if "error" in output:
        logger.warning("license_collector: trivy scan failed: %s", output["error"])
        return dict.fromkeys(packages, "UNKNOWN")

    by_name: dict[str, str] = {}
    for result in output.get("Results") or []:
        for lic in result.get("Licenses") or []:
            name = lic.get("PkgName")
            license_id = lic.get("Name")
            if name and license_id:
                by_name.setdefault(name, license_id)

    return {
        key: by_name.get(key.rsplit("@", 1)[0], "UNKNOWN") for key in packages
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_license_collector.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and typecheck**

Run: `cd apps/backend && uv run ruff check src/main_graph/subgraphs/analysis/agents/license_collector.py tests/unit/test_license_collector.py && uv run mypy src/main_graph/subgraphs/analysis/agents/license_collector.py`
Expected: no errors. `license_lookup_concurrency` in `src/utils/config.py` is now unused by this file — leave the setting itself alone (it's a public `Settings` field; removing it is out of scope for this task) but confirm with `grep -rn license_lookup_concurrency apps/backend/src` that no other file still depends on the code you just deleted.

- [ ] **Step 6: Commit**

```bash
cd apps/backend
git add src/main_graph/subgraphs/analysis/agents/license_collector.py tests/unit/test_license_collector.py
git commit -m "feat: back collect_licenses with trivy_license_scan"
```

---

## Task 7: `LicenseAgent.run()` passes `container` to `collect_licenses`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/agents/license_agent.py`
- Modify: `apps/backend/tests/unit/test_license_agent.py`

**Interfaces:**
- Consumes: `collect_licenses(prep, container)` (Task 6).
- Produces: no change to `LicenseAgent.run()`'s external signature.

- [ ] **Step 1: Update the failing test**

Open `apps/backend/tests/unit/test_license_agent.py`. Every call site that currently does something like:

```python
with patch("src.main_graph.subgraphs.analysis.agents.license_agent.collect_licenses", ...):
    bundle, tools, _ = await LicenseAgent().run(dispatch, prep, container=AsyncMock(), cache=None)
```

already passes `container=AsyncMock()` into `.run(...)` (check `test_license_agent_run_end_to_end`, `test_license_agent_treats_missing_project_license_as_unlicensed`, `test_license_agent_handles_legacy_dict_shaped_project_license`) — these should keep working unchanged since `LicenseAgent.run()`'s own signature isn't changing. The only thing to check is that any mock/patch of `collect_licenses` is an `AsyncMock` that tolerates being called with two positional/keyword args instead of one — `unittest.mock.AsyncMock` accepts any arguments by default, so no test changes should be required here. Run the existing suite first to confirm:

Run: `cd apps/backend && uv run pytest tests/unit/test_license_agent.py -v`
Expected: FAILS at this point only if the real (unpatched) `collect_licenses` gets exercised somewhere without `container` — inspect any failure output before assuming a fix is needed.

- [ ] **Step 2: Update `license_agent.py`'s single call site**

In `apps/backend/src/main_graph/subgraphs/analysis/agents/license_agent.py`, inside `run()`, change:

```python
        licenses = await collect_licenses(prep)
```

to:

```python
        licenses = await collect_licenses(prep, container)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_license_agent.py -v`
Expected: all PASS.

- [ ] **Step 4: Run the whole-tree byte-stability suite**

Run: `cd apps/backend && uv run pytest tests/subgraphs/test_whole_tree_agent_byte_stability.py -v`
Expected: PASS. If `test_license_agent_findings_are_byte_stable` patches `collect_licenses` directly, confirm the patch still works with the new two-argument call (it will, since a bare `patch(...)` replaces the whole callable regardless of the arguments the caller passes).

- [ ] **Step 5: Lint and typecheck**

Run: `cd apps/backend && uv run ruff check src/main_graph/subgraphs/analysis/agents/license_agent.py && uv run mypy src/main_graph/subgraphs/analysis/agents/license_agent.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd apps/backend
git add src/main_graph/subgraphs/analysis/agents/license_agent.py tests/unit/test_license_agent.py
git commit -m "feat: pass container through LicenseAgent to collect_licenses"
```

---

## Task 8: `dependency_graph.py` — CycloneDX adapter, async `build_dependency_graph`

This is the largest and riskiest task in this plan — it changes a function consumed by two discovery-graph nodes and deletes ~250 lines of lockfile-parsing code. Read the whole task before starting.

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/discovery/dependency_graph.py`
- Modify: `apps/backend/tests/unit/test_dependency_graph_helpers.py`

**Interfaces:**
- Consumes: `trivy_sbom_scan` (Task 3), `InputCacheDAO`/`cache_key`/`get_or_compute` (`src.db.input_cache`, unchanged).
- Produces: `async def build_dependency_graph(repo_path: str, package_manager: str, container: ContainerRunPort, docker_image: str, pkg: dict | None = None, cache: InputCacheDAO | None = None, repo_url: str = "", commit_sha: str = "") -> dict` — same `{"direct": ..., "packages": ...}` return shape as before. **Breaking change from sync to async** — consumed by Task 9 (`save_prep_result.py`) and Task 10 (`build_project_context`), which this plan updates in the same PR. `read_package_json`, `count_dependencies`, `is_direct`, `direct_dependents`, `dependents_of` are unchanged — do not modify them.

- [ ] **Step 1: Read the current file in full**

Run: `cat apps/backend/src/main_graph/subgraphs/discovery/dependency_graph.py` — confirm the functions you are about to delete (`_parse_npm_lock`, `_parse_pnpm_lock`, `_parse_yarn_lock`, `_parse_yarn_classic_lock`, `_parse_yarn_berry_lock`, `_split_pnpm_key`, `_yarn_dep_line_name_range`, `_yarn_descriptor_name`, `_collect_packages`, `build_dependency_graph`'s current sync body) and the functions you are keeping unchanged (`read_package_json`, `count_dependencies`, `is_direct`, `_package_name`, `direct_dependents`, `dependents_of`) against what this task describes below. If any kept function has changed since this plan was written, stop and re-read this task's Step 3 against the real current file before editing.

- [ ] **Step 2: Write the failing tests for the new adapter**

Add to `apps/backend/tests/unit/test_dependency_graph_helpers.py` (keep every existing test in this file untouched — they test `is_direct`/`direct_dependents`/`dependents_of` against the flat graph shape, which does not change):

```python
from unittest.mock import AsyncMock

import pytest

from src.main_graph.subgraphs.discovery.dependency_graph import build_dependency_graph


def _cyclonedx_doc(*, manifest_name: str, direct: list[dict], transitive_edges: dict[str, list[str]] | None = None) -> dict:
    """Build a minimal CycloneDX doc matching Trivy's verified shape: a root
    metadata.component, an "application"-typed manifest component the root
    depends on, and the manifest depending on the direct set.

    `transitive_edges` maps a direct dep's bom-ref to the bom-refs of its own
    children — the caller is responsible for also appending those child
    components/dependency entries to the returned doc (see
    test_build_dependency_graph_includes_transitive_edges for the pattern),
    since this helper only knows about the direct-dependency layer.
    """
    root_ref = "root-ref"
    manifest_ref = "manifest-ref"
    components = [{"bom-ref": manifest_ref, "type": "application", "name": manifest_name}]
    dependencies = [
        {"ref": root_ref, "dependsOn": [manifest_ref]},
        {"ref": manifest_ref, "dependsOn": [d["bom-ref"] for d in direct]},
    ]
    for d in direct:
        components.append({"bom-ref": d["bom-ref"], "type": "library", "name": d["name"], "version": d["version"]})
        dependencies.append({"ref": d["bom-ref"], "dependsOn": (transitive_edges or {}).get(d["bom-ref"], [])})
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "metadata": {"component": {"name": "/workspace", "bom-ref": root_ref}},
        "components": components,
        "dependencies": dependencies,
    }


@pytest.mark.asyncio
async def test_build_dependency_graph_adapts_cyclonedx_direct_deps():
    doc = _cyclonedx_doc(
        manifest_name="package-lock.json",
        direct=[{"bom-ref": "pkg:npm/express@4.22.2", "name": "express", "version": "4.22.2"}],
    )
    container = AsyncMock()
    container.run.return_value = (0, __import__("json").dumps(doc), "")

    graph = await build_dependency_graph(
        repo_path="/tmp/repo", package_manager="npm", container=container, docker_image="aquasec/trivy:0.71.2"
    )

    assert graph["direct"] == {"express": "4.22.2"}
    assert "express@4.22.2" in graph["packages"]


@pytest.mark.asyncio
async def test_build_dependency_graph_includes_transitive_edges():
    doc = _cyclonedx_doc(
        manifest_name="package-lock.json",
        direct=[{"bom-ref": "pkg:npm/express@4.22.2", "name": "express", "version": "4.22.2"}],
        transitive_edges={"pkg:npm/express@4.22.2": ["pkg:npm/accepts@1.3.8"]},
    )
    doc["components"].append({"bom-ref": "pkg:npm/accepts@1.3.8", "type": "library", "name": "accepts", "version": "1.3.8"})
    doc["dependencies"].append({"ref": "pkg:npm/accepts@1.3.8", "dependsOn": []})
    container = AsyncMock()
    container.run.return_value = (0, __import__("json").dumps(doc), "")

    graph = await build_dependency_graph(
        repo_path="/tmp/repo", package_manager="npm", container=container, docker_image="aquasec/trivy:0.71.2"
    )

    assert graph["packages"]["express@4.22.2"]["dependencies"] == ["accepts@1.3.8"]
    assert "accepts@1.3.8" in graph["packages"]


@pytest.mark.asyncio
async def test_build_dependency_graph_falls_back_to_package_json_on_scan_error(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"express": "^4.18.0"}}')
    container = AsyncMock()
    container.run.return_value = (127, "", "sh: trivy: not found")

    graph = await build_dependency_graph(
        repo_path=str(tmp_path), package_manager="npm", container=container, docker_image="aquasec/trivy:0.71.2"
    )

    assert graph == {"direct": {"express": "^4.18.0"}, "packages": {}}


@pytest.mark.asyncio
async def test_build_dependency_graph_falls_back_when_no_manifest_detected(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    container = AsyncMock()
    empty_doc = {"bomFormat": "CycloneDX", "specVersion": "1.7", "metadata": {}, "components": [], "dependencies": []}
    container.run.return_value = (0, __import__("json").dumps(empty_doc), "")

    graph = await build_dependency_graph(
        repo_path=str(tmp_path), package_manager="npm", container=container, docker_image="aquasec/trivy:0.71.2"
    )

    assert graph == {"direct": {}, "packages": {}}


@pytest.mark.asyncio
async def test_build_dependency_graph_uses_cache_when_available():
    from src.db.input_cache import cache_key

    cached_graph_doc = _cyclonedx_doc(manifest_name="package-lock.json", direct=[])

    class _FakeCache:
        def __init__(self):
            self.store = {
                cache_key("https://github.com/x/y", "sha1", "npm", "dependency_graph"): cached_graph_doc
            }

        async def get(self, key, max_age_seconds=None):
            return self.store.get(key)

        async def put(self, key, data):
            self.store[key] = data

    container = AsyncMock()
    graph = await build_dependency_graph(
        repo_path="/tmp/repo",
        package_manager="npm",
        container=container,
        docker_image="aquasec/trivy:0.71.2",
        cache=_FakeCache(),
        repo_url="https://github.com/x/y",
        commit_sha="sha1",
    )

    container.run.assert_not_awaited()
    assert graph == {"direct": {}, "packages": {}}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_dependency_graph_helpers.py -v -k build_dependency_graph`
Expected: FAIL — current `build_dependency_graph` is sync and takes `(repo_path, package_manager, pkg=None)`, no `container` argument.

- [ ] **Step 4: Rewrite `build_dependency_graph` and delete the lockfile parsers**

In `apps/backend/src/main_graph/subgraphs/discovery/dependency_graph.py`:

1. Delete these functions entirely: `build_dependency_graph` (old sync version), `_collect_packages`, `_parse_npm_lock`, `_parse_pnpm_lock`, `_split_pnpm_key`, `_parse_yarn_lock`, `_parse_yarn_classic_lock`, `_parse_yarn_berry_lock`, `_yarn_descriptor_name`, and any other `_yarn_*` helper only used by those parsers (check with `grep -n "_yarn_dep_line_name_range\|_yarn_descriptor_name" apps/backend/src/main_graph/subgraphs/discovery/dependency_graph.py` before deleting each one, since one might still be referenced by a function you're keeping).
2. Delete the now-unused `_PNPM_KEY_RE` constant and the `yaml` import if nothing else in the file uses it (check with `grep -n "yaml\." apps/backend/src/main_graph/subgraphs/discovery/dependency_graph.py` first).
3. Delete the `_RootKeys`/`_ParsedLock` type aliases (only used by the deleted parsers).
4. Keep `read_package_json`, `count_dependencies`, `is_direct`, `_package_name`, `direct_dependents`, `dependents_of` exactly as they are.
5. Add the new imports and functions:

```python
from src.db.input_cache import InputCacheDAO, cache_key, get_or_compute
from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.tools.trivy_cli import trivy_sbom_scan


def _graph_from_cyclonedx(doc: dict) -> dict | None:
    """Adapt a Trivy CycloneDX document into the flat {"direct", "packages"}
    shape every consumer already expects (see build_dependency_graph's
    docstring for why the shape is flat, not nested).

    Trivy's CycloneDX output puts the scanned manifest file (package-lock.json
    / pnpm-lock.yaml / yarn.lock) itself in `components` as an
    "application"-typed node: the root workspace component (in
    `metadata.component`, not in `components`) dependsOn that manifest node,
    and the manifest node's own dependsOn IS the direct-dependency set.
    Verified directly against real npm and pnpm lockfiles — see
    docs/superpowers/plans/2026-07-31-trivy-adoption.md.

    Returns None when no manifest component was found (empty repo, trivy
    scan error, or an ecosystem Trivy doesn't recognize) so the caller can
    fall back to package.json-declared ranges.
    """
    components = doc.get("components") or []
    dependencies = doc.get("dependencies") or []
    if not components or not dependencies:
        return None

    by_ref = {c["bom-ref"]: c for c in components if c.get("bom-ref")}
    depends_on = {d["ref"]: d.get("dependsOn", []) for d in dependencies if d.get("ref")}

    root_ref = (doc.get("metadata") or {}).get("component", {}).get("bom-ref")
    manifest_ref = next(
        (r for r in depends_on.get(root_ref, []) if by_ref.get(r, {}).get("type") == "application"),
        None,
    )
    if manifest_ref is None:
        return None

    direct_refs = depends_on.get(manifest_ref, [])
    direct = {
        by_ref[r]["name"]: by_ref[r].get("version", "")
        for r in direct_refs
        if r in by_ref
    }

    packages: dict[str, dict] = {}
    for ref, comp in by_ref.items():
        if comp.get("type") == "application":
            continue
        name = comp.get("name")
        if not name:
            continue
        version = comp.get("version", "")
        flat_key = f"{name}@{version}"
        children = sorted(
            f"{by_ref[c]['name']}@{by_ref[c].get('version', '')}"
            for c in depends_on.get(ref, [])
            if c in by_ref and by_ref[c].get("type") != "application"
        )
        packages[flat_key] = {"version": version, "dependencies": children}

    return {"direct": direct, "packages": packages}


async def build_dependency_graph(
    repo_path: str,
    package_manager: str,
    container: ContainerRunPort,
    docker_image: str,
    pkg: dict | None = None,
    cache: InputCacheDAO | None = None,
    repo_url: str = "",
    commit_sha: str = "",
) -> dict:
    """Return each direct dependency plus a flat, deduplicated graph of every
    package reachable from them, backed by a Trivy CycloneDX scan.

    The output is a flat, deduplicated graph — {"direct": {name: version},
    "packages": {"name@version": {"version", "dependencies": [child_key,
    ...]}}} — not a nested tree. A nested tree duplicates every shared
    package's full subtree under each path that reaches it, which blows up
    combinatorially on real repos and is too large for MongoDB to store.

    Falls back to package.json-declared ranges (no transitive data) when the
    scan fails or finds no manifest, e.g. an empty repo or a scan error.

    When `cache`/`repo_url`/`commit_sha` are all provided, the underlying
    Trivy scan is cached by (repo_url, commit_sha, package_manager) —
    callers (build_project_context, save_prep_result) both use this same
    cache key and run in the same job, so whichever runs first pays for the
    real scan and the second is a cache hit. Callers must only pass `cache`
    when the lockfile is a pure function of commit_sha (i.e. it was
    committed to the repo, not generated this run) — see save_prep_result's
    `lock_committed` check.
    """

    async def _scan() -> dict:
        return await trivy_sbom_scan(repo_path=repo_path, container=container)

    if cache is not None and repo_url and commit_sha:
        key = cache_key(repo_url, commit_sha, package_manager, "dependency_graph")
        doc = await get_or_compute(cache, key, _scan)
    else:
        doc = await _scan()

    graph = None if "error" in doc else _graph_from_cyclonedx(doc)
    if graph is not None:
        return graph

    logger.warning(
        "build_dependency_graph: trivy scan unusable, pm=%s, falling back to "
        "package.json-declared ranges",
        package_manager,
    )
    pkg = pkg if pkg is not None else read_package_json(repo_path)
    direct_names = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    return {"direct": dict(direct_names), "packages": {}}
```

Note the module docstring at the top of the file (currently describing the per-format parsers) also needs updating — replace it with a short description of the new Trivy-backed approach, keeping the "why flat, not nested" rationale that's now inside `build_dependency_graph`'s own docstring instead.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_dependency_graph_helpers.py -v`
Expected: all PASS, including every pre-existing `is_direct`/`direct_dependents`/`dependents_of` test untouched by this task.

- [ ] **Step 6: Lint and typecheck**

Run: `cd apps/backend && uv run ruff check src/main_graph/subgraphs/discovery/dependency_graph.py tests/unit/test_dependency_graph_helpers.py && uv run mypy src/main_graph/subgraphs/discovery/dependency_graph.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
cd apps/backend
git add src/main_graph/subgraphs/discovery/dependency_graph.py tests/unit/test_dependency_graph_helpers.py
git commit -m "feat: back build_dependency_graph with trivy CycloneDX scan"
```

---

## Task 9: `save_prep_result.py` — call the new async `build_dependency_graph`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/discovery/nodes/save_prep_result.py`
- Test: `apps/backend/tests/unit/subgraphs/discovery/test_save_prep_result.py` (check first with `find apps/backend/tests -iname "test_save_prep_result.py"` — if it doesn't exist, add the test described below to whichever discovery-orchestrator test file already exercises this node, e.g. `test_discovery_orchestrator.py`)

**Interfaces:**
- Consumes: `build_dependency_graph` (Task 8, now async and requiring `container`/`docker_image`).

- [ ] **Step 1: Locate the existing coverage for this node**

Run: `grep -rn "save_prep_result" apps/backend/tests --include="*.py" -l`
Read whichever file(s) this returns before writing new tests, to match existing fixture/mocking conventions for `DiscoveryState`/`get_services`.

- [ ] **Step 2: Write the failing test**

Add a test (in the file(s) found in Step 1, or a new `test_save_prep_result.py` if none exercises this node directly) asserting `save_prep_result` passes `container`/`docker_image` through to `build_dependency_graph` and that its own duplicate caching logic is gone:

```python
import pytest
from unittest.mock import AsyncMock, patch

from src.main_graph.subgraphs.discovery.nodes.save_prep_result import save_prep_result


@pytest.mark.asyncio
async def test_save_prep_result_calls_build_dependency_graph_with_container():
    from src.main_graph.subgraphs.discovery.nodes import save_prep_result as mod

    state = {
        "job_id": "j1",
        "repo_path": "/tmp/repo",
        "repo_url": "https://github.com/x/y",
        "commit_sha": "sha1",
        "detected_package_manager": "npm",
        "docker_image": "aquasec/trivy:0.71.2",
    }
    dao = AsyncMock()
    dao.save_prep.return_value = "prep-id-1"
    container = AsyncMock()
    config = {"configurable": {"services": {"result_dao": dao, "container": container}}}

    graph_mock = AsyncMock(return_value={"direct": {}, "packages": {}})
    with (
        patch.object(mod, "build_dependency_graph", graph_mock),
        patch.object(mod, "get_services", return_value={"result_dao": dao, "container": container, "input_cache": None}),
    ):
        result = await save_prep_result(state, config)

    graph_mock.assert_awaited_once()
    _, kwargs = graph_mock.call_args
    assert kwargs["container"] is container
    assert kwargs["docker_image"] == "aquasec/trivy:0.71.2"
    assert result == {"prep_result_id": "prep-id-1"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest <path-to-test-file> -v -k save_prep_result`
Expected: FAIL — current `save_prep_result` calls `build_dependency_graph(repo_path, pm)` synchronously with no `container`/`docker_image`.

- [ ] **Step 4: Update `save_prep_result.py`**

Replace the body of `apps/backend/src/main_graph/subgraphs/discovery/nodes/save_prep_result.py`'s `save_prep_result` function — the caching that used to wrap `_build_graph` is now inside `build_dependency_graph` itself (Task 8), so this simplifies:

```python
async def save_prep_result(state: DiscoveryState, config: RunnableConfig) -> dict:
    if state.get("discovery_error"):
        logger.info("save_prep_result: skipping due to discovery_error")
        return {}

    svc = get_services(config)
    dao = svc["result_dao"]
    cache = svc.get("input_cache")
    pm = state.get("detected_package_manager") or "unknown"
    repo_path = state.get("repo_path", "")
    repo_url = state.get("repo_url", "")
    commit_sha = state.get("commit_sha") or ""
    docker_image = state.get("docker_image") or "node:lts-alpine"

    # A freshly-generated lockfile was resolved against the live registry
    # this run, so it is NOT a pure function of commit_sha alone and must not
    # be cached indefinitely.
    lock_committed = not state.get("lockfile_generated")
    dep_graph = await build_dependency_graph(
        repo_path,
        pm,
        container=svc["container"],
        docker_image=docker_image,
        cache=cache if lock_committed else None,
        repo_url=repo_url,
        commit_sha=commit_sha,
    )

    result = PrepResult(
        job_id=state["job_id"],
        repo_path=repo_path,
        project_metadata=dict(state.get("project_metadata") or {}),
        manifest_files=state.get("manifest_files") or [],
        detected_package_manager=pm,
        docker_image=docker_image,
        repo_url=repo_url,
        commit_sha=commit_sha,
        dependency_graph=dep_graph,
        discovery_summary=state.get("project_context") or "",
        vector_store_id=state.get("vector_store_id") or "",
        codegraph_ready=state.get("codegraph_ready") or False,
    )
    prep_result_id = await dao.save_prep(result)
    logger.info("save_prep_result: saved prep_result_id=%s", prep_result_id)
    return {"prep_result_id": prep_result_id}
```

Remove the now-unused `cache_key`/`get_or_compute` import if nothing else in the file uses it (check with `grep -n "cache_key\|get_or_compute" apps/backend/src/main_graph/subgraphs/discovery/nodes/save_prep_result.py` after editing).

Note: `docker_image` here defaults to `"node:lts-alpine"` (today's Node-tooling default) but is passed to `build_dependency_graph`, which only uses it for the Trivy container image slot — this is a latent inconsistency worth flagging, not fixing in this task: `build_dependency_graph` should really receive `settings.trivy_image`, not `prep`'s Node docker image, since Trivy and Node tooling use different images. Fix this now: change the call above to pass `docker_image=settings.trivy_image` instead of `state.get("docker_image")`, and keep `docker_image=docker_image` (the Node image, unchanged) only in the `PrepResult(...)` construction below it. Import `from src.utils.config import settings` at the top of the file.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest <path-to-test-file> -v -k save_prep_result`
Expected: PASS. Update the test written in Step 2 to assert `kwargs["docker_image"] == settings.trivy_image` (import `settings` in the test file) instead of the state's `docker_image`, matching the correction made in Step 4.

- [ ] **Step 6: Lint and typecheck**

Run: `cd apps/backend && uv run ruff check src/main_graph/subgraphs/discovery/nodes/save_prep_result.py && uv run mypy src/main_graph/subgraphs/discovery/nodes/save_prep_result.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
cd apps/backend
git add src/main_graph/subgraphs/discovery/nodes/save_prep_result.py
git commit -m "feat: wire save_prep_result to async trivy-backed build_dependency_graph"
```

---

## Task 10: `build_project_context` — add `config`, call the same `build_dependency_graph`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py`
- Test: `apps/backend/tests/unit/subgraphs/discovery/test_build_dependency_summary.py` (check first with `find apps/backend/tests -iname "*build_dependency_summary*" -o -iname "*build_project_context*"`; if none exists, create it)

**Interfaces:**
- Consumes: `build_dependency_graph` (Task 8), `get_services` (`src.main_graph.config`, unchanged).

- [ ] **Step 1: Write the failing test**

Create (or extend) `apps/backend/tests/unit/subgraphs/discovery/test_build_dependency_summary.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.build_dependency_summary import (
    build_project_context,
)


@pytest.mark.asyncio
async def test_build_project_context_passes_container_and_cache():
    from src.main_graph.subgraphs.discovery.nodes import build_dependency_summary as mod

    state = {
        "repo_path": "/tmp/repo",
        "concern": "check for vulnerabilities",
        "detected_package_manager": "npm",
        "repo_url": "https://github.com/x/y",
        "commit_sha": "sha1",
    }
    container = AsyncMock()
    cache = AsyncMock()
    config = {"configurable": {"services": {"container": container, "input_cache": cache}}}

    graph_mock = AsyncMock(return_value={"direct": {"express": "4.18.0"}, "packages": {}})
    llm_response = MagicMock(content="a summary")
    mod._llm.ainvoke = AsyncMock(return_value=llm_response)

    with (
        patch.object(mod, "build_dependency_graph", graph_mock),
        patch.object(
            mod, "get_services", return_value={"container": container, "input_cache": cache}
        ),
    ):
        result = await build_project_context(state, config)

    graph_mock.assert_awaited_once()
    _, kwargs = graph_mock.call_args
    assert kwargs["container"] is container
    assert kwargs["cache"] is cache
    assert result["project_metadata"]["direct_dependencies_count"] == 1


@pytest.mark.asyncio
async def test_build_project_context_skips_scan_on_discovery_error():
    state = {"discovery_error": "clone failed"}
    result = await build_project_context(state, config={})
    assert result["project_context"] == "Discovery failed: clone failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/discovery/test_build_dependency_summary.py -v`
Expected: FAIL — `build_project_context` currently takes only `(state)`, no `config`, and calls the old sync `build_dependency_graph`.

- [ ] **Step 3: Update `build_dependency_summary.py`**

Replace the full contents of `apps/backend/src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py`:

```python
"""Node: build_project_context — lightweight LLM summary from package.json."""

from __future__ import annotations

import json
import logging
import textwrap

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.dependency_graph import (
    build_dependency_graph,
    count_dependencies,
    read_package_json,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState, ProjectMetadata
from src.utils.config import settings
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_SYSTEM = textwrap.dedent("""\
    You are analyzing a Node.js project. Given its package.json contents and the \
user's concern, write a concise summary (3-6 sentences, ≤ 150 words) that:
    - Names the project and its stated purpose
    - Lists key dependency groups most relevant to the concern
    - Flags anything immediately notable (scripts, workspaces, unusual dependencies)
    Output only the summary text.
    """).strip()


async def build_project_context(state: DiscoveryState, config: RunnableConfig) -> dict:
    error = state.get("discovery_error")
    if error:
        return {
            "project_metadata": ProjectMetadata(
                name="unknown",
                package_manager="unknown",
                direct_dependencies_count=0,
                transitive_dependencies_count=0,
            ),
            "project_context": f"Discovery failed: {error}",
        }

    svc = get_services(config)
    repo_path = state.get("repo_path", "")
    concern = state.get("concern", "")
    pkg = read_package_json(repo_path)
    pm = state.get("detected_package_manager", "npm")

    # A freshly-generated lockfile was resolved against the live registry
    # this run, so it is NOT a pure function of commit_sha alone and must not
    # be cached indefinitely — mirrors save_prep_result's identical check.
    # This node and save_prep_result share the same trivy-scan cache key, so
    # whichever of the two runs first (this one, per discovery graph order)
    # pays for the real scan and the other is a cache hit.
    lock_committed = not state.get("lockfile_generated")
    graph = await build_dependency_graph(
        repo_path,
        pm,
        container=svc["container"],
        docker_image=settings.trivy_image,
        pkg=pkg,
        cache=svc.get("input_cache") if lock_committed else None,
        repo_url=state.get("repo_url", ""),
        commit_sha=state.get("commit_sha") or "",
    )
    direct, transitive = count_dependencies(graph)

    metadata = ProjectMetadata(
        name=pkg.get("name", "unknown"),
        package_manager=pm,
        direct_dependencies_count=direct,
        transitive_dependencies_count=transitive,
    )

    pkg_summary = json.dumps(
        {
            k: pkg.get(k)
            for k in (
                "name",
                "version",
                "description",
                "scripts",
                "dependencies",
                "devDependencies",
                "workspaces",
            )
            if pkg.get(k)
        },
        indent=2,
    )[:3000]  # cap to avoid token overflow

    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": f"Concern: {concern}\n\npackage.json:\n{pkg_summary}",
            },
        ]
    )

    logger.info(
        "build_project_context: project=%s pm=%s direct=%d",
        metadata["name"],
        pm,
        direct,
    )
    return {"project_metadata": metadata, "project_context": response.content}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/discovery/test_build_dependency_summary.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the discovery orchestrator suite**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/discovery/test_discovery_orchestrator.py -v`
Expected: PASS. LangGraph passes `config` to any node whose signature accepts it — since `build_project_context` didn't take `config` before, confirm `graph.py`'s `builder.add_node(BUILD_PROJECT_CONTEXT, build_project_context)` needs no change (it doesn't; LangGraph introspects the node function's signature at call time).

- [ ] **Step 6: Lint and typecheck**

Run: `cd apps/backend && uv run ruff check src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py tests/unit/subgraphs/discovery/test_build_dependency_summary.py && uv run mypy src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
cd apps/backend
git add src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py tests/unit/subgraphs/discovery/test_build_dependency_summary.py
git commit -m "feat: wire build_project_context to async trivy-backed build_dependency_graph"
```

---

## Task 11: Full-suite verification and cleanup pass

**Files:** none new — this task verifies Tasks 1-10 together and cleans up anything a single-task lint pass couldn't catch (cross-file unused imports, stale docstrings).

- [ ] **Step 1: Run the entire backend test suite**

Run: `cd apps/backend && uv run pytest -q`
Expected: all tests pass except `tests/unit/test_graph_routing.py::test_pipeline_includes_remediation_between_analysis_and_report`, which is a pre-existing failure unrelated to this plan (confirmed via `git stash` against unrelated in-progress remediation-subgraph work — see `bugfix_npm_audit_pnpm_silent_zero_findings` memory for how this was verified before). If any other test fails, stop and fix it before proceeding — do not mark this task complete with an unexplained failure.

- [ ] **Step 2: Grep for dead references to removed code**

```bash
grep -rn "npm_audit" apps/backend/src
grep -rn "_parse_npm_lock\|_parse_pnpm_lock\|_parse_yarn_lock" apps/backend/src
```

The second grep must return zero results. The first will legitimately still
match two places, which are OUT OF SCOPE for this plan and must NOT be
touched: `apps/backend/src/main_graph/tools/npm_cli.py`'s own `npm_audit`
function definition (still a real, independently-used tool — do not delete
it), and `apps/backend/src/main_graph/subgraphs/remediation/deepagent/
nodes.py`, which calls `npm_audit` directly as part of the remediation
subgraph's own verify step, entirely independent of `VulnerabilityAgent`.
Confirm the first grep's only matches are those two files; if `npm_audit`
appears anywhere else under `src/` (e.g. still imported by
`vulnerability_agent.py`), that call site was missed in an earlier task —
find and fix it before proceeding.

- [ ] **Step 3: Ruff and mypy across the whole backend**

Run: `cd apps/backend && uv run ruff check . && uv run mypy src`
Expected: no errors. Fix anything flagged before proceeding — do not suppress with inline ignores unless a specific line was already using that pattern before this plan.

- [ ] **Step 4: Manual smoke check of the discovery→analysis flow (no mocks)**

This plan changes real Docker invocations (`trivy fs ...`) that unit tests mock out entirely — run one real job end-to-end before considering this plan done. Use whatever local dev flow this repo already documents for running a job against a real repo (check `apps/backend/docs/development-setup.md` and `apps/backend/scripts/run_subgraph.py` first); at minimum, confirm:
- `docker run --rm aquasec/trivy:0.71.2 --version` succeeds (image is pulled and pinned correctly).
- A job against a small real npm repo with at least one known-vulnerable dependency (e.g. a throwaway repo depending on `lodash@4.17.15`) produces a non-empty `VulnerabilityAgent` finding.
- A second run against the same commit completes the vuln/license/SBOM scans in well under the first run's time (confirms `cache_volume` is actually being reused, not just unit-tested).

- [ ] **Step 5: Commit any cleanup from Steps 2-3**

Only if Steps 2 or 3 required changes:

```bash
cd apps/backend
git add -A
git commit -m "chore: cleanup after trivy adoption (dead refs, lint)"
```
