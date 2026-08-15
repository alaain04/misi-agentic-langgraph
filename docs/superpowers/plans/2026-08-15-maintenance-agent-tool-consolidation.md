# Maintenance Agent Tool Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `MaintenanceAgent`'s three overlapping, inconsistently-flagging
npm tools (`unmaintained_packages`, `high_risk_packages`, `package_reputation`)
with a single raw-data tool (`package_health_data`), and move all risk
judgment into the system prompt so a stale-but-heavily-downloaded package
(e.g. `class-validator`) can no longer surface as a contradictory finding.

**Architecture:** One new tool in `external_api.py` does a single bulk npm
registry fetch per repo and returns per-package facts with no thresholds or
flagging. The three old tools (and their now-unused threshold constant) are
deleted. `MaintenanceAgent`'s `_agent_tools()` and `system_prompt` are
rewritten to call the new tool once and reason over recency vs. downloads
directly, per `docs/superpowers/specs/2026-08-15-maintenance-agent-tool-consolidation.md`.

**Tech Stack:** Python 3, pytest + pytest-asyncio, `unittest.mock` (`patch`,
`AsyncMock`), the project's `@register`-based tool registry
(`src/main_graph/tools/registry.py`).

## Global Constraints

- Package manager: `uv` (never `pip` directly) — run tests via `uv run pytest`.
- No emoji in code, commit messages, or comments.
- Only `MaintenanceAgent` uses the three old tools — confirmed via
  `codegraph_explore` + grep against `src/` and `tests/`; safe to delete
  outright, no deprecation shim needed.
- `typosquat_detection` and `_POPULAR_PACKAGES` in `external_api.py` are
  unrelated (name-similarity, not health) and must not be touched.
- The ~1,000 weekly-downloads low-adoption anchor moves from enforced tool
  code (`_LOW_WEEKLY_DOWNLOADS`) to prompt guidance text only — this is an
  intentional, accepted trade-off (spec D2), not a bug to "fix" by adding it
  back as code.

---

### Task 1: Add `package_health_data` tool

**Files:**
- Create: `apps/backend/tests/unit/tools/test_package_health_data.py`
- Modify: `apps/backend/src/main_graph/tools/external_api.py` (insert new
  function; do not touch the three old tools yet — that's Task 2)

**Interfaces:**
- Produces: `package_health_data(repo_path: str) -> dict`, registered in
  `TOOL_REGISTRY` under the name `"package_health_data"`. Return shape:
  ```python
  {
      "packages": [
          {
              "package": str,
              "created": str,           # ISO8601 or "" if missing
              "last_modified": str,     # ISO8601 or "" if missing
              "weekly_downloads": int | None,
              "maintainer_count": int,
              "latest_version": str,
          },
          # or, when that package's npm metadata fetch failed:
          {"package": str, "error": str},
      ],
      "checked": int,      # len(deps_to_check), capped at 30
      "total_deps": int,   # len(deps) before the 30-cap
  }
  ```
  Task 3 (`MaintenanceAgent`) consumes this exact shape.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/tests/unit/tools/test_package_health_data.py`:

```python
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.tools.registry import TOOL_REGISTRY

_NOW = datetime.now(UTC)
_RECENT = (_NOW - timedelta(days=10)).isoformat()
_OLD = (_NOW - timedelta(days=1000)).isoformat()


def _meta(
    created: str, modified: str, maintainer_count: int, latest: str = "1.0.0"
) -> dict:
    return {
        "time": {"created": created, "modified": modified},
        "maintainers": [{"name": f"m{i}"} for i in range(maintainer_count)],
        "dist-tags": {"latest": latest},
    }


@pytest.mark.asyncio
async def test_returns_raw_facts_for_healthy_package():
    meta = _meta(_OLD, _RECENT, maintainer_count=2, latest="2.3.1")
    with (
        patch(
            "src.main_graph.tools.external_api._load_pkg",
            return_value={"dependencies": {"healthy-pkg": "1.0.0"}},
        ),
        patch(
            "src.main_graph.tools.external_api._npm_metadata",
            AsyncMock(return_value=meta),
        ),
        patch(
            "src.main_graph.tools.external_api._npm_weekly_downloads",
            AsyncMock(return_value=50_000),
        ),
    ):
        result = await TOOL_REGISTRY["package_health_data"](repo_path="/fake")

    assert result["packages"] == [
        {
            "package": "healthy-pkg",
            "created": _OLD,
            "last_modified": _RECENT,
            "weekly_downloads": 50_000,
            "maintainer_count": 2,
            "latest_version": "2.3.1",
        }
    ]
    assert result["checked"] == 1
    assert result["total_deps"] == 1


@pytest.mark.asyncio
async def test_stale_but_high_downloads_returned_without_flagging():
    """The tool does no risk judgment -- a stale, heavily-downloaded package
    (e.g. class-validator) comes back as plain data, not dropped or marked
    risky. Risk judgment moved to the agent's prompt, not the tool."""
    meta = _meta(_OLD, _OLD, maintainer_count=1, latest="0.14.0")
    with (
        patch(
            "src.main_graph.tools.external_api._load_pkg",
            return_value={"dependencies": {"class-validator": "0.14.0"}},
        ),
        patch(
            "src.main_graph.tools.external_api._npm_metadata",
            AsyncMock(return_value=meta),
        ),
        patch(
            "src.main_graph.tools.external_api._npm_weekly_downloads",
            AsyncMock(return_value=11_998_815),
        ),
    ):
        result = await TOOL_REGISTRY["package_health_data"](repo_path="/fake")

    assert result["packages"] == [
        {
            "package": "class-validator",
            "created": _OLD,
            "last_modified": _OLD,
            "weekly_downloads": 11_998_815,
            "maintainer_count": 1,
            "latest_version": "0.14.0",
        }
    ]


@pytest.mark.asyncio
async def test_metadata_error_is_returned_not_dropped():
    with (
        patch(
            "src.main_graph.tools.external_api._load_pkg",
            return_value={"dependencies": {"broken-pkg": "1.0.0"}},
        ),
        patch(
            "src.main_graph.tools.external_api._npm_metadata",
            AsyncMock(return_value={"error": "404 Not Found"}),
        ),
        patch(
            "src.main_graph.tools.external_api._npm_weekly_downloads",
            AsyncMock(return_value=None),
        ),
    ):
        result = await TOOL_REGISTRY["package_health_data"](repo_path="/fake")

    assert result["packages"] == [{"package": "broken-pkg", "error": "404 Not Found"}]


def test_package_health_data_is_registered():
    assert "package_health_data" in TOOL_REGISTRY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/backend/tests/unit/tools/test_package_health_data.py -v`
(from `apps/backend/`: `uv run pytest tests/unit/tools/test_package_health_data.py -v`)
Expected: FAIL — `KeyError: 'package_health_data'` (not yet registered).

- [ ] **Step 3: Implement `package_health_data`**

In `apps/backend/src/main_graph/tools/external_api.py`, insert the new
function immediately before the `@register("unmaintained_packages", ...)`
block (i.e. right after `package_reputation` ends at line 172, before line
175). Do not remove anything yet:

```python
@register(
    "package_health_data",
    "Reports raw npm registry health facts (release recency, weekly downloads, "
    "maintainer count) for every direct dependency. Returns data only — no risk "
    "judgment or flagging; the caller decides what counts as a maintenance risk.",
)
async def package_health_data(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    deps = list(_all_deps(pkg).keys())
    deps_to_check = deps[:30]  # limit to avoid rate limiting
    metas, downloads = await asyncio.gather(
        asyncio.gather(*[_npm_metadata(d) for d in deps_to_check]),
        asyncio.gather(*[_npm_weekly_downloads(d) for d in deps_to_check]),
    )
    packages = []
    for dep, meta, weekly_downloads in zip(deps_to_check, metas, downloads):
        if "error" in meta:
            packages.append({"package": dep, "error": meta["error"]})
            continue
        time_data = meta.get("time", {})
        packages.append(
            {
                "package": dep,
                "created": time_data.get("created", ""),
                "last_modified": time_data.get("modified", ""),
                "weekly_downloads": weekly_downloads,
                "maintainer_count": len(meta.get("maintainers", [])),
                "latest_version": meta.get("dist-tags", {}).get("latest", ""),
            }
        )
    return {
        "packages": packages,
        "checked": len(deps_to_check),
        "total_deps": len(deps),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `apps/backend/`): `uv run pytest tests/unit/tools/test_package_health_data.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/tools/external_api.py apps/backend/tests/unit/tools/test_package_health_data.py
git commit -m "feat: add package_health_data raw-facts npm tool"
```

---

### Task 2: Remove the three old tools and the old test file

**Files:**
- Modify: `apps/backend/src/main_graph/tools/external_api.py` (delete
  `package_reputation`, `unmaintained_packages`, `_LOW_WEEKLY_DOWNLOADS`,
  `high_risk_packages` and their `@register` blocks)
- Delete: `apps/backend/tests/unit/tools/test_high_risk_packages.py`

**Interfaces:**
- Consumes: nothing new — this task only removes code. `package_health_data`
  from Task 1 is untouched.
- Produces: `TOOL_REGISTRY` no longer contains `"package_reputation"`,
  `"unmaintained_packages"`, or `"high_risk_packages"`. Task 3 depends on
  these being gone (it removes the corresponding imports in
  `maintenance_agent.py`).

- [ ] **Step 1: Confirm no other callers before deleting**

Run (from `apps/backend/`):
```bash
grep -rn "unmaintained_packages\|high_risk_packages\|package_reputation" src/ tests/
```
Expected: only hits inside `external_api.py` (the definitions themselves),
`maintenance_agent.py` (handled in Task 3), and `test_maintenance_agent.py`
(handled in Task 3) — no other consumer. This matches what was already
confirmed during spec research; re-run here as a pre-deletion safety check
since the tree may have changed.

- [ ] **Step 2: Delete the three tools and the constant from `external_api.py`**

Remove these three blocks entirely (the `@register(...)` decorator plus the
function body for each):

1. The `package_reputation` function and its `@register("package_reputation", ...)`
   block (originally lines 145-172, immediately before the
   `package_health_data` function added in Task 1).
2. The `unmaintained_packages` function and its `@register("unmaintained_packages", ...)`
   block (originally lines 175-196, immediately after `package_health_data`).
3. The `_LOW_WEEKLY_DOWNLOADS = 1000` constant and the `high_risk_packages`
   function with its `@register("high_risk_packages", ...)` block
   (originally lines 267-319, between `typosquat_detection` and
   `_package_name_variants`).

Leave `_npm_metadata`, `_npm_weekly_downloads`, `_POPULAR_PACKAGES`,
`typosquat_detection`, `_package_name_variants`, `_mentions_package`, and
`web_search` untouched — `package_health_data` and `typosquat_detection`
still depend on the two `_npm_*` and `_all_deps`/`_load_pkg` helpers.

- [ ] **Step 3: Delete the old tool's test file**

```bash
git rm apps/backend/tests/unit/tools/test_high_risk_packages.py
```

- [ ] **Step 4: Run the full tools test suite**

Run (from `apps/backend/`): `uv run pytest tests/unit/tools/ -v`
Expected: PASS — `test_high_risk_packages.py` is gone,
`test_package_health_data.py` still passes, nothing else in that directory
references the removed tools.

- [ ] **Step 5: Confirm nothing else in the repo still imports the removed names**

Run (from `apps/backend/`):
```bash
python -c "import ast, sys; sys.exit(0)"  # sanity: python available
grep -rn "unmaintained_packages\|high_risk_packages\|package_reputation" src/ tests/
```
Expected: only `maintenance_agent.py` and `test_maintenance_agent.py` remain
(both fixed in Task 3). If anything else shows up, stop and investigate
before continuing — do not delete a still-used symbol.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/tools/external_api.py
git rm apps/backend/tests/unit/tools/test_high_risk_packages.py
git commit -m "refactor: remove unmaintained_packages, high_risk_packages, package_reputation"
```

---

### Task 3: Rewire `MaintenanceAgent` onto `package_health_data`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/agents/maintenance_agent.py`
- Modify: `apps/backend/tests/unit/test_maintenance_agent.py`
- Modify: `apps/backend/docs/e2e-test-catalog.md:167`

**Interfaces:**
- Consumes: `package_health_data` from Task 1 (`src.main_graph.tools.external_api.package_health_data`).
- Produces: `MaintenanceAgent._agent_tools()` returns `[package_health_data]`.
  `MaintenanceAgent.system_prompt` reasons over `package_health_data`'s
  output shape directly (no other task depends on the exact prompt wording).

- [ ] **Step 1: Update the import and `_agent_tools()`**

In `apps/backend/src/main_graph/subgraphs/analysis/agents/maintenance_agent.py`,
replace:

```python
from src.main_graph.tools.external_api import (
    high_risk_packages,
    package_reputation,
    unmaintained_packages,
)
```

with:

```python
from src.main_graph.tools.external_api import package_health_data
```

Replace:

```python
    def _agent_tools(self) -> list:
        return [unmaintained_packages, high_risk_packages, package_reputation]
```

with:

```python
    def _agent_tools(self) -> list:
        return [package_health_data]
```

- [ ] **Step 2: Rewrite `system_prompt`**

Replace the entire `system_prompt` class attribute with:

```python
    system_prompt = """
        You are a package maintenance and health specialist for Node.js dependencies.
        Your task: {hypothesis}
        Packages to focus on: {packages}

        Available tools:
        {tool_descriptions}

        Investigation strategy:
        1. Call package_health_data once to get npm registry facts (created \
date, last release date, weekly downloads, maintainer count, latest version) \
for every direct dependency in the repo.
        2. For each package, weigh release recency against weekly_downloads \
before deciding it is a risk:
           - Strong current adoption overrides staleness alone. A package \
with weekly_downloads at or above roughly 1,000 is meaningfully in active \
use — many mature, stable libraries go a long time between releases \
without that meaning anything is wrong. Never flag such a package as a \
maintenance risk based on last_modified age by itself.
           - A package IS a maintenance risk if: last_modified is more than \
12 months old AND weekly_downloads is low (below roughly 1,000) or \
missing/errored — OR the package was created less than 90 days ago AND \
weekly_downloads is low or missing/errored.
        3. Record the package name, last_modified date, weekly_downloads, \
and risk rationale in each FindingNote so the downloads-vs-staleness \
tradeoff is visible to a reviewer.

        Rules on maintainer count:
        - A single-maintainer package is NOT, by itself, a finding. Most healthy,
          widely-used npm packages (lodash, many @nestjs/* scopes, etc.) have one
          maintainer. Never create or justify a finding using maintainer count alone
          — only the recency/downloads criteria in step 2 above count as risk.

        Scope:
        - Only assess DIRECT dependencies (declared in package.json). Do not
          create maintenance findings for transitive dependencies — their health
          is the direct parent's responsibility and is not directly actionable.

        Rules:
        - Never repeat a tool call with the same arguments.
        - Set finalize=true when you have assessed all flagged packages.
        - After {max_iter} iterations, set finalize=true regardless.
        - confidence > 0.8: you have data for all focused packages.
        - confidence 0.5-0.8: partial data, some packages returned no results.
        - confidence < 0.5: tools returned errors or no data.
        """
```

- [ ] **Step 3: Update the mocked tool name in `test_maintenance_agent.py`**

In `apps/backend/tests/unit/test_maintenance_agent.py`, in
`test_maintenance_drops_transitive_findings`, replace:

```python
        AsyncMock(return_value=(_bundle(findings), ["unmaintained_packages"], 1)),
```

with:

```python
        AsyncMock(return_value=(_bundle(findings), ["package_health_data"], 1)),
```

and replace:

```python
    assert tools == ["unmaintained_packages"]
```

with:

```python
    assert tools == ["package_health_data"]
```

(`BaseAgent.run` is mocked in this test, so this is a string-literal
update only — it doesn't exercise the real tool.)

- [ ] **Step 4: Update the doc reference**

In `apps/backend/docs/e2e-test-catalog.md`, on the line currently reading
(around line 167):

```
| 3.4 | Maintenance findings are absent for every transitive package with real evidence of staleness available (i.e. not just "none happened to be flagged") — cross-check by manually running `unmaintained_packages`/`high_risk_packages` against a known-stale transitive in the target repo and confirming the agent still doesn't surface it as a finding | Q5, strongest form | not yet run — needs a repo with a deliberately old/abandoned transitive dependency to be a meaningful test, not just an absence-of-evidence result |
```

replace `` `unmaintained_packages`/`high_risk_packages` `` with
`` `package_health_data` ``.

- [ ] **Step 5: Run the maintenance agent tests**

Run (from `apps/backend/`): `uv run pytest tests/unit/test_maintenance_agent.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Run the full backend unit test suite**

Run (from `apps/backend/`): `uv run pytest tests/unit/ -v`
Expected: PASS — confirms nothing else in the unit suite referenced the
removed tool names or the old prompt text.

- [ ] **Step 7: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/agents/maintenance_agent.py apps/backend/tests/unit/test_maintenance_agent.py apps/backend/docs/e2e-test-catalog.md
git commit -m "refactor: rewire MaintenanceAgent onto package_health_data"
```

---

## Manual verification (not automated)

The spec's success criteria include an end-to-end check that can't be made
deterministic (it depends on the LLM correctly applying the new prompt
guidance, not just on tool-level data shape, which Task 1's tests already
cover): run the existing `scripts/e2e_check.py` pattern, or a manual
`/analyze` call, against a repo that declares `class-validator` (or another
stale-but-heavily-downloaded package) as a direct dependency, with a concern
like "outdated or unmaintained dependencies". Confirm the resulting
`analysis_report` does NOT include a maintenance finding for that package,
while a genuinely stale, low-download direct dependency in the same repo
still produces one. This is a one-time sanity check after Task 3, not a
CI-gated step.

## Self-review notes

- **Spec coverage:** D1 → Task 1. D2 (tool-level override removed, prompt
  carries the judgment) → Task 2 (removal) + Task 3 Step 2 (prompt). D3
  (`_agent_tools()` returns one tool) → Task 3 Step 1. All four "Success
  criteria" bullets from the spec map to a task; the E2E-shaped one is
  called out explicitly as manual, matching the spec's own "manual/E2E
  check" phrasing.
- **Placeholder scan:** no TBD/TODO; every step has literal code or an exact
  grep/pytest command.
- **Type consistency:** `package_health_data`'s return shape (Task 1) is
  used identically in Task 3's prompt description (created, last_modified,
  weekly_downloads, maintainer_count, latest_version) — no drift.
