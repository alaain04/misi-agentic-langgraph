# Reliability D1 — PAT-Based Private-Repo Clone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a caller supply a per-request GitHub PAT so `clone_repo` can
authenticate against private repositories, without the token ever touching
persisted storage, logs, or the cloned repo's own git config.

**Architecture:** The token flows from the `/analyze` request body through
`config["configurable"]` (never graph state, never `JobMetadata`) into
`clone_repo`, which passes it to the container as a bare Docker `-e`
env-var name (value delivered via process-environment inheritance, never
appearing in the logged command) and uses it inside the container via a
process-scoped `git -c http.extraHeader` override (never written to
`.git/config`).

**Tech Stack:** Python 3.12, FastAPI, Pydantic, LangGraph, pytest +
pytest-asyncio, unittest.mock (AsyncMock/MagicMock/patch) — no new
dependencies.

## Global Constraints

- The token must never be written into `Job`/`JobMetadata` (what
  `dao.create()` persists to Mongo), never into graph *state*, and never
  appear as a literal in any string passed to `logger.info` or `logger.error`.
- `resume_analysis` does **not** gain a token parameter — by the time a job
  reaches the HITL gate, `clone_repo` has already run.
- No behavior change for the existing anonymous-clone path when no token is
  supplied — every existing test in
  `tests/unit/subgraphs/discovery/test_discovery_orchestrator.py` must keep
  passing unmodified.
- No token format validation beyond "present or absent" — an invalid token
  surfaces as a `git clone` auth failure through the existing
  `discovery_error` path.
- Spec: `docs/superpowers/specs/2026-07-23-reliability-d1-pat-private-repo-design.md`.

---

### Task 1: Request/Job models + route wiring

**Files:**
- Modify: `apps/backend/src/api/schemas.py`
- Modify: `apps/backend/src/models/job.py`
- Modify: `apps/backend/src/api/routes.py`
- Test: `apps/backend/tests/unit/test_job.py` (extend)
- Test: `apps/backend/tests/unit/test_routes.py` (create)

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `AnalysisRequest.github_token: str | None`,
  `JobMetadata.used_pat: bool`. Task 2 consumes `request.github_token` (as
  the value passed into `run_analysis(..., github_token=...)`) — this task
  is where that value first enters the system, but the actual call to
  `run_analysis` is modified in Task 2's file (`job_runner.py`'s signature
  changes there); this task only changes what `routes.py` passes.

- [ ] **Step 1: Write the failing model test**

Append to `apps/backend/tests/unit/test_job.py`:

```python
def test_job_metadata_used_pat_defaults_false():
    job = Job(metadata=JobMetadata(repo_url=_REPO_URL, concern="security"))
    assert job.metadata.used_pat is False


def test_job_metadata_stores_used_pat():
    job = Job(
        metadata=JobMetadata(repo_url=_REPO_URL, concern="security", used_pat=True)
    )
    assert job.metadata.used_pat is True
    doc = job.to_doc()
    assert doc["metadata"]["used_pat"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/test_job.py -k used_pat -v`
Expected: FAIL with `TypeError: JobMetadata() got an unexpected keyword argument 'used_pat'`

- [ ] **Step 3: Add `used_pat` to `JobMetadata`**

In `apps/backend/src/models/job.py`, change:

```python
class JobMetadata(BaseModel):
    repo_url: str
    concern: str
    autopilot: bool = False
```

to:

```python
class JobMetadata(BaseModel):
    repo_url: str
    concern: str
    autopilot: bool = False
    # Audit signal only — never the token itself. Set from
    # bool(request.github_token) in the /analyze route handler.
    used_pat: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/test_job.py -k used_pat -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Add `github_token` to the request schema**

In `apps/backend/src/api/schemas.py`, change:

```python
class AnalysisRequest(BaseModel):
    repo_url: str
    concern: str
    autopilot: bool = False
```

to:

```python
class AnalysisRequest(BaseModel):
    repo_url: str
    concern: str
    autopilot: bool = False
    # Per-request PAT for private-repo clone (Workstream D1). Used for this
    # job only; never persisted to JobMetadata or any other stored document.
    github_token: str | None = None
```

- [ ] **Step 6: Write the failing route tests**

Create `apps/backend/tests/unit/test_routes.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from src.api.routes import analyze
from src.api.schemas import AnalysisRequest

_REPO_URL = "https://github.com/example/repo"


@pytest.mark.asyncio
async def test_analyze_sets_used_pat_when_token_provided():
    dao = AsyncMock()
    request = AnalysisRequest(
        repo_url=_REPO_URL, concern="security", github_token="ghp_abc123"
    )

    with patch("src.api.routes.run_analysis", new=AsyncMock()):
        await analyze(request, dao=dao)

    created_job = dao.create.call_args.args[0]
    assert created_job.metadata.used_pat is True


@pytest.mark.asyncio
async def test_analyze_used_pat_false_without_token():
    dao = AsyncMock()
    request = AnalysisRequest(repo_url=_REPO_URL, concern="security")

    with patch("src.api.routes.run_analysis", new=AsyncMock()):
        await analyze(request, dao=dao)

    created_job = dao.create.call_args.args[0]
    assert created_job.metadata.used_pat is False


@pytest.mark.asyncio
async def test_analyze_token_never_persisted_in_job_doc():
    dao = AsyncMock()
    request = AnalysisRequest(
        repo_url=_REPO_URL, concern="security", github_token="ghp_SECRETVALUE"
    )

    with patch("src.api.routes.run_analysis", new=AsyncMock()):
        await analyze(request, dao=dao)

    created_job = dao.create.call_args.args[0]
    doc = created_job.to_doc()
    assert "ghp_SECRETVALUE" not in str(doc)


@pytest.mark.asyncio
async def test_analyze_passes_token_to_run_analysis():
    dao = AsyncMock()
    request = AnalysisRequest(
        repo_url=_REPO_URL, concern="security", github_token="ghp_abc123"
    )

    with patch("src.api.routes.run_analysis", new=AsyncMock()) as mock_run:
        await analyze(request, dao=dao)

    assert mock_run.call_args.kwargs["github_token"] == "ghp_abc123"


@pytest.mark.asyncio
async def test_analyze_passes_none_when_no_token():
    dao = AsyncMock()
    request = AnalysisRequest(repo_url=_REPO_URL, concern="security")

    with patch("src.api.routes.run_analysis", new=AsyncMock()) as mock_run:
        await analyze(request, dao=dao)

    assert mock_run.call_args.kwargs["github_token"] is None
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_routes.py -v`
Expected: FAIL — `run_analysis` doesn't yet accept `github_token`, and
`analyze()` doesn't yet set `used_pat` or pass the token through.

- [ ] **Step 8: Update the route handler**

In `apps/backend/src/api/routes.py`, change the `analyze` function from:

```python
@router.post("/analyze", status_code=202)
async def analyze(
    request: AnalysisRequest,
    dao: JobRepositoryPort = Depends(get_job_repo),
):
    job = Job(
        metadata=JobMetadata(
            repo_url=request.repo_url,
            concern=request.concern,
            autopilot=request.autopilot,
        )
    )
    await dao.create(job)
    asyncio.create_task(
        run_analysis(
            job_id=job.id,
            repo_url=job.metadata.repo_url,
            concern=job.metadata.concern,
            autopilot=request.autopilot,
            dao=dao,
        )
    )
    return {"trace_id": job.id, "status": job.status}
```

to:

```python
@router.post("/analyze", status_code=202)
async def analyze(
    request: AnalysisRequest,
    dao: JobRepositoryPort = Depends(get_job_repo),
):
    job = Job(
        metadata=JobMetadata(
            repo_url=request.repo_url,
            concern=request.concern,
            autopilot=request.autopilot,
            used_pat=bool(request.github_token),
        )
    )
    await dao.create(job)
    asyncio.create_task(
        run_analysis(
            job_id=job.id,
            repo_url=job.metadata.repo_url,
            concern=job.metadata.concern,
            autopilot=request.autopilot,
            dao=dao,
            github_token=request.github_token,
        )
    )
    return {"trace_id": job.id, "status": job.status}
```

Note: `run_analysis` does not yet accept `github_token` — this will make
`test_analyze_passes_token_to_run_analysis` and
`test_analyze_passes_none_when_no_token` fail at the `run_analysis(...)`
call site with a `TypeError` when the mock is bypassed... actually since
`run_analysis` is patched with `AsyncMock()` in these tests, the patched
mock accepts any kwargs, so these two tests will actually PASS already at
this step (the mock doesn't enforce the real signature). The real
signature change happens in Task 2 — that's fine, the route-level test
only asserts what `routes.py` *passes*, not what `job_runner.py` *accepts*.

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_routes.py tests/unit/test_job.py -v`
Expected: PASS (5 + existing test_job.py tests, all green)

- [ ] **Step 10: Commit**

```bash
cd apps/backend
git add src/api/schemas.py src/models/job.py src/api/routes.py tests/unit/test_job.py tests/unit/test_routes.py
git commit -m "feat: accept per-request github_token, never persist it (D1 task 1)"
```

---

### Task 2: Thread the token through job_runner into the graph configurable

**Files:**
- Modify: `apps/backend/src/services/job_runner.py`
- Modify: `apps/backend/src/main_graph/config.py`
- Test: `apps/backend/tests/unit/services/test_job_runner.py` (extend)

**Interfaces:**
- Consumes: `run_analysis(..., github_token: str | None)` is called by
  `routes.py` (Task 1) — this task makes that signature real.
- Produces: `config["configurable"]["github_token"]`, present only when a
  token was supplied. `PipelineConfigurable.github_token: NotRequired[str]`.
  Task 4 (`clone_repo.py`) consumes this via `svc.get("github_token")`.

- [ ] **Step 1: Write the failing job_runner tests**

Append to `apps/backend/tests/unit/services/test_job_runner.py`:

```python
@pytest.mark.asyncio
async def test_run_analysis_threads_github_token_into_configurable():
    dao = _make_dao()
    captured: dict = {}

    async def fake_stream(*args, **kwargs):
        captured["config"] = args[1]
        yield {"prep": {}}

    with (
        patch("src.services.job_runner.main_graph") as mock_graph,
        patch("src.services.job_runner.clear_cache"),
        patch("src.services.job_runner.get_result_dao"),
        patch("src.services.job_runner.get_input_cache"),
    ):
        mock_graph.astream = fake_stream
        mock_graph.aget_state = AsyncMock(return_value=MagicMock(values={}))
        await run_analysis(
            "job-5",
            "https://github.com/x/y",
            "security",
            autopilot=False,
            dao=dao,
            github_token="ghp_abc123",
        )

    assert captured["config"]["configurable"]["github_token"] == "ghp_abc123"


@pytest.mark.asyncio
async def test_run_analysis_omits_github_token_when_not_provided():
    dao = _make_dao()
    captured: dict = {}

    async def fake_stream(*args, **kwargs):
        captured["config"] = args[1]
        yield {"prep": {}}

    with (
        patch("src.services.job_runner.main_graph") as mock_graph,
        patch("src.services.job_runner.clear_cache"),
        patch("src.services.job_runner.get_result_dao"),
        patch("src.services.job_runner.get_input_cache"),
    ):
        mock_graph.astream = fake_stream
        mock_graph.aget_state = AsyncMock(return_value=MagicMock(values={}))
        await run_analysis(
            "job-6", "https://github.com/x/y", "security", autopilot=False, dao=dao
        )

    assert "github_token" not in captured["config"]["configurable"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/services/test_job_runner.py -k github_token -v`
Expected: FAIL with `TypeError: run_analysis() got an unexpected keyword argument 'github_token'`

- [ ] **Step 3: Update `PipelineConfigurable`**

In `apps/backend/src/main_graph/config.py`, change:

```python
class PipelineConfigurable(TypedDict):
    job_repo: JobRepositoryPort
    container: ContainerRunPort
    docker_tool: BaseTool
    result_dao: ResultDAO
    # Optional: absent in lightweight contexts (run_subgraph script, tests).
    # Consumers guard with svc.get("input_cache").
    input_cache: NotRequired[InputCacheDAO]
```

to:

```python
class PipelineConfigurable(TypedDict):
    job_repo: JobRepositoryPort
    container: ContainerRunPort
    docker_tool: BaseTool
    result_dao: ResultDAO
    # Optional: absent in lightweight contexts (run_subgraph script, tests).
    # Consumers guard with svc.get("input_cache").
    input_cache: NotRequired[InputCacheDAO]
    # Optional: per-request PAT for private-repo clone (Workstream D1).
    # Never persisted — threaded from the /analyze request body only, and
    # absent from graph state entirely. Consumers guard with
    # svc.get("github_token").
    github_token: NotRequired[str]
```

- [ ] **Step 4: Update `_build_config` and `run_analysis`**

In `apps/backend/src/services/job_runner.py`, change `_build_config` from:

```python
def _build_config(job_id: str, dao: JobRepositoryPort, cost_cb: CostCallback) -> dict:
    container = DockerContainerAdapter()
    return {
        "configurable": {
            "thread_id": job_id,
            "job_repo": dao,
            "container": container,
            "docker_tool": make_docker_tool(container),
            "result_dao": get_result_dao(),
            "input_cache": get_input_cache(),
        },
        "callbacks": [cost_cb],
    }
```

to:

```python
def _build_config(
    job_id: str,
    dao: JobRepositoryPort,
    cost_cb: CostCallback,
    github_token: str | None = None,
) -> dict:
    container = DockerContainerAdapter()
    configurable = {
        "thread_id": job_id,
        "job_repo": dao,
        "container": container,
        "docker_tool": make_docker_tool(container),
        "result_dao": get_result_dao(),
        "input_cache": get_input_cache(),
    }
    if github_token:
        configurable["github_token"] = github_token
    return {
        "configurable": configurable,
        "callbacks": [cost_cb],
    }
```

Change `run_analysis`'s signature and its `_build_config` call from:

```python
async def run_analysis(
    job_id: str,
    repo_url: str,
    concern: str,
    autopilot: bool,
    dao: JobRepositoryPort,
) -> None:
    await dao.update_status(job_id, JobStatus.running)
    await dao.start_artifact(job_id, PREP)
    cost_cb = CostCallback()
    config = _build_config(job_id, dao, cost_cb)
```

to:

```python
async def run_analysis(
    job_id: str,
    repo_url: str,
    concern: str,
    autopilot: bool,
    dao: JobRepositoryPort,
    github_token: str | None = None,
) -> None:
    await dao.update_status(job_id, JobStatus.running)
    await dao.start_artifact(job_id, PREP)
    cost_cb = CostCallback()
    config = _build_config(job_id, dao, cost_cb, github_token=github_token)
```

(`resume_analysis` is unchanged — it does not accept or need `github_token`,
per the Global Constraints.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/services/test_job_runner.py -v`
Expected: PASS (all tests in the file, including the 2 new ones)

- [ ] **Step 6: Commit**

```bash
cd apps/backend
git add src/services/job_runner.py src/main_graph/config.py tests/unit/services/test_job_runner.py
git commit -m "feat: thread github_token into graph configurable, not state (D1 task 2)"
```

---

### Task 3: `secret_env` passthrough on the container port (no-log-leak proof)

**Files:**
- Modify: `apps/backend/src/domain/ports/container_run_port.py`
- Modify: `apps/backend/src/main_graph/adapters/docker_container_adapter.py`
- Test: `apps/backend/tests/unit/test_docker_container_adapter.py` (create)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `ContainerRunPort.run(..., secret_env: dict[str, str] | None = None)`.
  Task 4 (`clone_repo.py`) calls `container.run(..., secret_env=...)`.

- [ ] **Step 1: Write the failing adapter tests**

Create `apps/backend/tests/unit/test_docker_container_adapter.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter


def _mock_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    proc = AsyncMock()
    proc.communicate.return_value = (stdout, stderr)
    proc.returncode = returncode
    return proc


@pytest.mark.asyncio
async def test_run_without_secret_env_unchanged_behavior():
    adapter = DockerContainerAdapter()
    with patch(
        "asyncio.create_subprocess_exec", return_value=_mock_proc()
    ) as mock_exec:
        await adapter.run(image="alpine/git", command="echo hi")

    assert mock_exec.call_args.kwargs["env"] is None
    assert "-e" not in mock_exec.call_args.args


@pytest.mark.asyncio
async def test_run_with_secret_env_never_puts_value_in_cmd_args():
    adapter = DockerContainerAdapter()
    with patch(
        "asyncio.create_subprocess_exec", return_value=_mock_proc()
    ) as mock_exec:
        await adapter.run(
            image="alpine/git",
            command="echo hi",
            secret_env={"GIT_TOKEN": "ghp_SECRETVALUE"},
        )

    call_args = mock_exec.call_args.args
    assert "ghp_SECRETVALUE" not in call_args
    assert "-e" in call_args
    assert "GIT_TOKEN" in call_args


@pytest.mark.asyncio
async def test_run_with_secret_env_passes_value_only_via_env_kwarg():
    adapter = DockerContainerAdapter()
    with patch(
        "asyncio.create_subprocess_exec", return_value=_mock_proc()
    ) as mock_exec:
        await adapter.run(
            image="alpine/git",
            command="echo hi",
            secret_env={"GIT_TOKEN": "ghp_SECRETVALUE"},
        )

    env = mock_exec.call_args.kwargs["env"]
    assert env["GIT_TOKEN"] == "ghp_SECRETVALUE"


@pytest.mark.asyncio
async def test_run_with_secret_env_never_logged(caplog):
    adapter = DockerContainerAdapter()
    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc()):
        with caplog.at_level("INFO"):
            await adapter.run(
                image="alpine/git",
                command="echo hi",
                secret_env={"GIT_TOKEN": "ghp_SECRETVALUE"},
            )

    assert "ghp_SECRETVALUE" not in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_docker_container_adapter.py -v`
Expected: FAIL with `TypeError: DockerContainerAdapter.run() got an unexpected keyword argument 'secret_env'`

- [ ] **Step 3: Update the port interface**

In `apps/backend/src/domain/ports/container_run_port.py`, change:

```python
from abc import ABC, abstractmethod


class ContainerRunPort(ABC):
    @abstractmethod
    async def run(
        self,
        image: str,
        command: str,
        volume: str | None = None,
        run_as_root: bool = False,
    ) -> tuple[int, str, str]:
        """Run a container. Returns (returncode, stdout, stderr)."""
        ...
```

to:

```python
from abc import ABC, abstractmethod


class ContainerRunPort(ABC):
    @abstractmethod
    async def run(
        self,
        image: str,
        command: str,
        volume: str | None = None,
        run_as_root: bool = False,
        secret_env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Run a container. Returns (returncode, stdout, stderr).

        `secret_env` values are delivered via Docker's bare `-e VARNAME`
        form (name only, no `=value`) so they flow through process
        environment inheritance only — the value never appears in the
        constructed command list, which adapters log verbatim.
        """
        ...
```

- [ ] **Step 4: Update `DockerContainerAdapter`**

In `apps/backend/src/main_graph/adapters/docker_container_adapter.py`, change:

```python
class DockerContainerAdapter(ContainerRunPort):
    async def run(
        self,
        image: str,
        command: str,
        volume: str | None = None,
        run_as_root: bool = False,
    ) -> tuple[int, str, str]:
        cmd = ["docker", "run", "--rm"]
        if volume:
            cmd += ["-v", volume]
        if not run_as_root:
            cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
        cmd += ["--entrypoint", "sh", image, "-c", command]
        logger.info("docker: %s", " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
```

to:

```python
class DockerContainerAdapter(ContainerRunPort):
    async def run(
        self,
        image: str,
        command: str,
        volume: str | None = None,
        run_as_root: bool = False,
        secret_env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        cmd = ["docker", "run", "--rm"]
        if volume:
            cmd += ["-v", volume]
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
```

(The rest of the method — timeout handling, decode, return — is unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_docker_container_adapter.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the full existing test suite to confirm no regression**

Run: `cd apps/backend && uv run pytest -q`
Expected: all tests pass (no existing caller of `container.run(...)` breaks,
since `secret_env` is optional with a `None` default)

- [ ] **Step 7: Commit**

```bash
cd apps/backend
git add src/domain/ports/container_run_port.py src/main_graph/adapters/docker_container_adapter.py tests/unit/test_docker_container_adapter.py
git commit -m "feat: secret_env passthrough on ContainerRunPort, never logged (D1 task 3)"
```

---

### Task 4: `clone_repo` uses the token for authenticated clone

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/discovery/nodes/clone_repo.py`
- Test: `apps/backend/tests/unit/subgraphs/discovery/test_discovery_orchestrator.py` (extend)

**Interfaces:**
- Consumes: `svc.get("github_token")` (Task 2's `PipelineConfigurable`),
  `container.run(..., secret_env=...)` (Task 3's `ContainerRunPort`).
- Produces: nothing consumed by later tasks — this is the last task.

- [ ] **Step 1: Write the failing clone_repo tests**

Append to
`apps/backend/tests/unit/subgraphs/discovery/test_discovery_orchestrator.py`:

```python
@pytest.mark.asyncio
async def test_clone_repo_uses_token_when_configured(tmp_path):
    container = AsyncMock()
    container.run.return_value = (0, "", "")
    config = {
        "configurable": {
            "container": container,
            "github_token": "ghp_secret123",
        }
    }

    with patch("src.main_graph.subgraphs.discovery.nodes.clone_repo.os.makedirs"):
        with patch(
            "src.main_graph.subgraphs.discovery.nodes.clone_repo.os.path.abspath",
            return_value=str(tmp_path),
        ):
            await clone_repo(_BASE_STATE, config)

    clone_call = container.run.call_args_list[0]
    assert "$GIT_TOKEN" in clone_call.kwargs["command"]
    assert "ghp_secret123" not in clone_call.kwargs["command"]
    assert clone_call.kwargs["secret_env"] == {"GIT_TOKEN": "ghp_secret123"}


@pytest.mark.asyncio
async def test_clone_repo_without_token_matches_existing_behavior(tmp_path):
    container = AsyncMock()
    container.run.return_value = (0, "", "")

    with patch("src.main_graph.subgraphs.discovery.nodes.clone_repo.os.makedirs"):
        with patch(
            "src.main_graph.subgraphs.discovery.nodes.clone_repo.os.path.abspath",
            return_value=str(tmp_path),
        ):
            result = await clone_repo(_BASE_STATE, _config(container=container))

    clone_call = container.run.call_args_list[0]
    assert "$GIT_TOKEN" not in clone_call.kwargs["command"]
    assert clone_call.kwargs.get("secret_env") is None
    assert result["repo_path"] == str(tmp_path)


@pytest.mark.asyncio
async def test_clone_repo_rev_parse_call_has_no_secret_env(tmp_path):
    container = AsyncMock()
    container.run.return_value = (0, "abc123\n", "")
    config = {
        "configurable": {
            "container": container,
            "github_token": "ghp_secret123",
        }
    }

    with patch("src.main_graph.subgraphs.discovery.nodes.clone_repo.os.makedirs"):
        with patch(
            "src.main_graph.subgraphs.discovery.nodes.clone_repo.os.path.abspath",
            return_value=str(tmp_path),
        ):
            await clone_repo(_BASE_STATE, config)

    rev_parse_call = container.run.call_args_list[1]
    assert rev_parse_call.kwargs.get("secret_env") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/discovery/test_discovery_orchestrator.py -k token -v`
Expected: FAIL — `clone_repo` doesn't yet read `github_token` or pass
`secret_env`; the command string never contains `$GIT_TOKEN`.

- [ ] **Step 3: Update `clone_repo.py`**

In `apps/backend/src/main_graph/subgraphs/discovery/nodes/clone_repo.py`,
change:

```python
"""Node: clone_repo — shallow-clone the repository into a temp directory."""

import logging
import os

from langchain_core.runnables import RunnableConfig

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_GIT_IMAGE = "alpine/git"


async def clone_repo(state: DiscoveryState, config: RunnableConfig) -> dict:
    """Shallow-clone the repository. Sets repo_path; sets discovery_error on failure."""
    svc = get_services(config)
    container: ContainerRunPort = svc["container"]

    job_id = state["job_id"]
    repo_url = state["repo_url"]
    tmp_dir = os.path.abspath(f"tmp/debug_job_{job_id}")
    os.makedirs(tmp_dir, exist_ok=True)

    rc, _out, stderr = await container.run(
        image=_GIT_IMAGE,
        command=f"git clone --depth=1 --single-branch {repo_url} /workspace",
        volume=f"{tmp_dir}:/workspace",
        run_as_root=True,
    )

    if rc != 0:
        logger.error("clone_repo: failed rc=%d stderr=%s", rc, stderr[:300])
        return {
            "repo_path": tmp_dir,
            "discovery_error": stderr.strip() or "git clone failed",
        }

    logger.info("clone_repo: success repo_url=%s", repo_url)

    sha_rc, sha_out, _sha_err = await container.run(
        image=_GIT_IMAGE,
        command="cd /workspace && git rev-parse HEAD",
        volume=f"{tmp_dir}:/workspace",
        run_as_root=True,
    )
    commit_sha = sha_out.strip() if sha_rc == 0 else ""
    return {"repo_path": tmp_dir, "commit_sha": commit_sha}
```

to:

```python
"""Node: clone_repo — shallow-clone the repository into a temp directory."""

import logging
import os

from langchain_core.runnables import RunnableConfig

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_GIT_IMAGE = "alpine/git"


def _clone_command(
    repo_url: str, github_token: str | None
) -> tuple[str, dict[str, str] | None]:
    """Build the clone command. When a token is present, auth is injected via
    a process-scoped `git -c http.extraHeader` override (never written to
    the cloned repo's own .git/config) referencing $GIT_TOKEN — the actual
    value is delivered to the shell only via the container's environment
    (see ContainerRunPort.run's secret_env), never as a literal here.
    """
    if github_token:
        command = (
            'git -c http.extraHeader="AUTHORIZATION: basic '
            "$(printf 'x-access-token:%s' \"$GIT_TOKEN\" | base64)\" "
            f"clone --depth=1 --single-branch {repo_url} /workspace"
        )
        return command, {"GIT_TOKEN": github_token}
    return f"git clone --depth=1 --single-branch {repo_url} /workspace", None


async def clone_repo(state: DiscoveryState, config: RunnableConfig) -> dict:
    """Shallow-clone the repository. Sets repo_path; sets discovery_error on failure."""
    svc = get_services(config)
    container: ContainerRunPort = svc["container"]
    github_token = svc.get("github_token")

    job_id = state["job_id"]
    repo_url = state["repo_url"]
    tmp_dir = os.path.abspath(f"tmp/debug_job_{job_id}")
    os.makedirs(tmp_dir, exist_ok=True)

    command, secret_env = _clone_command(repo_url, github_token)
    rc, _out, stderr = await container.run(
        image=_GIT_IMAGE,
        command=command,
        volume=f"{tmp_dir}:/workspace",
        run_as_root=True,
        secret_env=secret_env,
    )

    if rc != 0:
        logger.error("clone_repo: failed rc=%d stderr=%s", rc, stderr[:300])
        return {
            "repo_path": tmp_dir,
            "discovery_error": stderr.strip() or "git clone failed",
        }

    logger.info("clone_repo: success repo_url=%s", repo_url)

    sha_rc, sha_out, _sha_err = await container.run(
        image=_GIT_IMAGE,
        command="cd /workspace && git rev-parse HEAD",
        volume=f"{tmp_dir}:/workspace",
        run_as_root=True,
    )
    commit_sha = sha_out.strip() if sha_rc == 0 else ""
    return {"repo_path": tmp_dir, "commit_sha": commit_sha}
```

**IMPORTANT — verify the exact quoting compiles to valid Python before
running tests.** The `_clone_command` f-string in the plan above uses a
mix of single- and double-quoted literal segments concatenated together
specifically so the *resulting* shell string reads:

```
git -c http.extraHeader="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GIT_TOKEN" | base64)" clone --depth=1 --single-branch <repo_url> /workspace
```

Copy the Python source exactly as shown — do not "clean up" the quoting
without re-deriving that the resulting shell string is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/discovery/test_discovery_orchestrator.py -v`
Expected: PASS — all existing clone_repo/inspect_repo/install_deps tests
plus the 3 new token tests, all green.

- [ ] **Step 5: Run the full suite, ruff, and mypy**

Run:
```bash
cd apps/backend
uv run pytest -q
uv run ruff check .
uv run mypy src scripts
```
Expected: all green, no regressions anywhere in the suite.

- [ ] **Step 6: Commit**

```bash
cd apps/backend
git add src/main_graph/subgraphs/discovery/nodes/clone_repo.py tests/unit/subgraphs/discovery/test_discovery_orchestrator.py
git commit -m "feat: clone_repo authenticates via PAT when configured (D1 task 4)"
```

---

## Post-implementation: live verification (manual, not part of the automated suite)

The spec flags this explicitly as a known gap: unit tests mock
`ContainerRunPort` and cannot catch a shell-escaping mistake in the
`http.extraHeader` snippet. Before treating this feature as trustworthy,
run it for real against one already-provisioned private fixture:

```bash
curl -s -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/alaain04/misi-e2e-validation-cve-direct",
    "concern": "known security vulnerabilities",
    "autopilot": true,
    "github_token": "<a real PAT with repo scope for that account>"
  }'
```

Poll `/analyze/{trace_id}` to completion; confirm `status: done`, no
`discovery_error`, and `metadata.used_pat: true`. This is the point where
Workstream B's fixture corpus (`scripts/corpus_check.py
--assert-live`/`CORPUS_PAT_AVAILABLE=1`) becomes runnable for the first
time — a natural immediate follow-up once this lands, not part of this plan.

**Also verify with both token shapes:** the final whole-branch review flagged
that `printf 'x-access-token:%s' "$GIT_TOKEN" | base64` could theoretically
wrap output past 76 columns for a long fine-grained PAT (`github_pat_...`,
~93 chars) on a `base64` that line-wraps by default, which would corrupt the
`AUTHORIZATION` header. `alpine/git`'s busybox `base64` does not wrap, so
this is expected to be fine — but the live smoke test should exercise it
with a classic `ghp_...` token AND a fine-grained `github_pat_...` token to
close this out, not just one.
