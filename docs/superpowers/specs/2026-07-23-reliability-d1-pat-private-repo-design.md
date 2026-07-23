# Reliability D1 — PAT-Based Private-Repo Clone Design

**Date:** 2026-07-23
**Status:** approved (design), pending spec review
**Roadmap:** `docs/superpowers/roadmap.md` Workstream D ("Repo access & credentials").
Unblocks Workstream B's fixture corpus (`docs/e2e-test-catalog.md` Group 8),
whose 8 `misi-e2e-validation-*` repos are all private and cannot currently be
cloned — `clone_repo` does 100% anonymous `git clone` with zero auth support.

## Problem

The pipeline can only analyze public repositories. `clone_repo`
(`apps/backend/src/main_graph/subgraphs/discovery/nodes/clone_repo.py`)
interpolates `repo_url` directly into an unauthenticated `git clone` command.
This blocks two things: analyzing a caller's own private projects, and
running the fixture corpus (Workstream B) live at all, since D1 was
deliberately deferred out of that workstream's scope.

## Scope decision

**Per-request token (multi-tenant)**, not a single operator-configured PAT.
The caller supplies a token in the `/analyze` request body; it is used for
that job only and never persisted. This is a larger security surface than a
single global PAT, so the design below is built around one non-negotiable
constraint: **the token must never be written to the job store, logs, or any
artifact** (the roadmap's own words). Every design choice traces back to
that constraint, verified against the actual code paths, not assumed.

## Non-goals

- **D2 (write access + PR creation)** and **D3 (consent & audit model)** are
  separate workstreams. This is read-only clone auth.
- No token format validation beyond "non-empty" — an invalid token surfaces
  as a `git clone` auth failure, which already produces a `discovery_error`
  through the existing failure path. No new error taxonomy needed.
- No changes to the existing anonymous-clone behavior for public repos —
  this is purely additive and optional per-request.

## Architecture

### 1. The token never touches anything persisted

Traced the exact path: `POST /analyze` (`apps/backend/src/api/routes.py:22-46`)
builds `Job(metadata=JobMetadata(repo_url=..., concern=..., autopilot=...))`
and calls `dao.create(job)` — this is what gets written to Mongo. It then
calls `run_analysis(job_id=..., repo_url=..., concern=..., autopilot=...,
dao=...)`, which builds the graph's initial **state** input
(`{"repo_url":..., "concern":..., "job_id":..., "autopilot":..., "messages": []}`)
and a separate **config** dict (`_build_config` in
`apps/backend/src/services/job_runner.py`, whose `configurable` key already
carries `container`, `job_repo`, `result_dao`, `input_cache`).

The token must go through neither the `Job`/`JobMetadata` document nor graph
*state* — only through `config["configurable"]`, the same DI channel
`input_cache` already uses. This is deliberate: the graph's checkpointer is
`InMemorySaver()` (`apps/backend/src/main_graph/graph.py:37`, confirmed —
not Mongo-backed), and `configurable` is not part of the state
`_finalize()` reads back via `main_graph.aget_state(config).values`. So the
token's blast radius is: request handler → `run_analysis` parameter →
`_build_config` → `configurable["github_token"]` → read once by
`clone_repo`. Nothing else in the pipeline ever sees it.

- `AnalysisRequest` (`apps/backend/src/api/schemas.py`) gains
  `github_token: str | None = None`.
- The `analyze()` route handler reads `request.github_token` locally,
  computes `used_pat = bool(request.github_token)`, and passes the token
  straight into `run_analysis(..., github_token=request.github_token)` —
  never into `JobMetadata`.
- `JobMetadata` gains `used_pat: bool = False` — an audit signal, safe to
  persist since it carries no secret material. It flows automatically into
  `AnalysisStatusResponse.metadata` (already `JobMetadata`-typed) with no
  extra response-model wiring.
- `run_analysis` gains `github_token: str | None = None`, passed to
  `_build_config(..., github_token=github_token)`, which sets
  `configurable["github_token"]` only when present.
- `PipelineConfigurable` (`apps/backend/src/main_graph/config.py`) gains
  `github_token: NotRequired[str]`.
- `resume_analysis` does **not** gain this parameter. By the time a job
  reaches the HITL gate, `clone_repo` has already run — the token is only
  needed for the initial `run_analysis` call.

### 2. The token must never hit the logs

`DockerContainerAdapter.run()`
(`apps/backend/src/main_graph/adapters/docker_container_adapter.py:26`) does
`logger.info("docker: %s", " ".join(cmd))` — the **entire** docker argv,
unconditionally, with no redaction. Any design that embeds the token as a
literal in `cmd` or in the `command` string leaks it into plaintext logs
immediately.

Fix: use Docker's bare `-e VARNAME` form (name only, no `=value`), which
tells Docker to read the value from the *calling process's own
environment* rather than from the CLI argv:

- `ContainerRunPort.run()` (`apps/backend/src/domain/ports/container_run_port.py`)
  gains an optional `secret_env: dict[str, str] | None = None` parameter.
- `DockerContainerAdapter.run()` appends bare `-e {key}` (just the name —
  safe to log) to `cmd` for each key in `secret_env`, and passes
  `env={**os.environ, **secret_env}` to `asyncio.create_subprocess_exec`
  (scoped to that one subprocess call — no global `os.environ` mutation).
- The existing `logger.info("docker: %s", " ".join(cmd))` line needs no
  change — `cmd` never contains a secret value, only the bare variable name.

### 3. Inside the container, don't persist the credential into the clone

`git clone https://<token>@host/repo` writes the token into the cloned
repo's own `.git/config`, which would sit on disk in the ephemeral job
directory for the job's lifetime — unnecessary persistence beyond the
moment of cloning. Instead, use `git -c http.extraHeader=...`, a
process-scoped config override that is **never written to any file**. This
is the same mechanism GitHub Actions' own checkout action uses.

`clone_repo.py`'s command construction becomes conditional:

```python
if github_token:
    command = (
        'git -c http.extraHeader="AUTHORIZATION: basic '
        '$(printf \'x-access-token:%s\' "$GIT_TOKEN" | base64)" '
        f"clone --depth=1 --single-branch {repo_url} /workspace"
    )
    secret_env = {"GIT_TOKEN": github_token}
else:
    command = f"git clone --depth=1 --single-branch {repo_url} /workspace"
    secret_env = None
```

Only the literal text `$GIT_TOKEN` (a shell variable reference) ever appears
in the Python-constructed `command` string — the shell inside the container
substitutes the real value at runtime, sourced from the env Docker injected
per §2. The second `git rev-parse HEAD` call (commit SHA capture) needs no
auth — it operates on the already-cloned local repo.

### 4. Zero risk to the existing public-repo path

`clone_repo` only takes the authenticated branch when `github_token` is
present (read via `svc.get("github_token")`); absent, behavior is
byte-for-byte what it is today. Public-repo jobs, the existing fixture
corpus's SKIP-until-D1 runner, and every existing test are unaffected.

## Error handling

- Missing/invalid token → `git clone` fails with an auth error from
  GitHub → existing `rc != 0` path in `clone_repo` sets `discovery_error`
  from `stderr` → job reaches `failed` status through the existing flow. No
  new error handling needed; the token is simply one more way clone can
  fail, already handled.
- Token present but repo is actually public → works fine (GitHub accepts
  the header regardless); no special-casing needed.

## Testing

- Unit tests for `clone_repo.py`: with `github_token` present in the
  configurable, assert the command string contains `$GIT_TOKEN` (the
  reference) and never the literal token value; assert
  `container.run(...)` is called with `secret_env={"GIT_TOKEN": <token>}`.
  Without a token, assert byte-identical behavior to today (existing tests
  in `tests/unit/subgraphs/discovery/test_discovery_orchestrator.py` must
  keep passing unmodified).
- Unit test for `DockerContainerAdapter.run()`: given `secret_env`, assert
  the constructed `cmd` list contains bare `-e GIT_TOKEN` and never the
  value; assert the value is only ever present in the `env=` kwarg passed to
  `create_subprocess_exec` (mocked), never in `cmd`. This is the test that
  directly proves §2's no-log-leak property.
- Unit test for the `/analyze` route: given `github_token` in the request,
  assert `JobMetadata.used_pat is True` and assert the token value itself
  never appears anywhere in the `Job`/`JobMetadata` object passed to
  `dao.create(...)` (grep the serialized doc for the token value in the
  test as a belt-and-suspenders check).
- No live private-repo test in this PR — validating against the real
  fixture corpus (Workstream B, already built and waiting) is the natural
  first live use, done as a manual follow-up once this merges, not blocking
  the PR itself.
- **Known gap:** unit tests mock `ContainerRunPort`, so they can assert the
  Python-side command string and `secret_env` are constructed correctly, but
  cannot catch a shell-escaping mistake in the `http.extraHeader` snippet
  itself (i.e. whether it's actually valid POSIX shell once it reaches
  alpine's `sh`/busybox ash). That can only be proven by a real run. The
  manual live-validation step above against one real fixture (e.g.
  `misi-e2e-validation-cve-direct`) is what actually closes this gap —
  treat the PR as unverified end-to-end until that run succeeds.

## Deliverables

1. `AnalysisRequest.github_token`, `JobMetadata.used_pat` (`schemas.py`, `job.py`).
2. `routes.py`: read token locally, never write it into `JobMetadata`.
3. `job_runner.py`: `run_analysis`/`_build_config` thread `github_token` into
   `configurable` only.
4. `config.py`: `PipelineConfigurable.github_token: NotRequired[str]`.
5. `container_run_port.py` + `docker_container_adapter.py`: `secret_env`
   parameter, bare `-e KEY` passthrough, no logging change needed.
6. `clone_repo.py`: conditional authenticated command construction.
7. Unit tests per the Testing section above.
