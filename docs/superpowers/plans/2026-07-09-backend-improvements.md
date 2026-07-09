# Backend Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve data quality and intelligence in the backend: persist autopilot mode, surface HITL history to the conductor LLM, add full inline evidence objects to findings, fix the tool_runner repo_path injection bug, add transitive dep awareness with web search, and persist the npm dependency tree.

**Architecture:** All changes are backend-only with two small frontend type updates. Tasks 3, 4, and 6 all modify `conductor.py` and must run in that order. The `EvidenceRef` model (Task 4) is foundational to Task 6's conductor prompt. Task 7 (dep tree) is independent.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, MongoDB (pymongo async), Pydantic v2, httpx, pytest-asyncio. Package manager: `uv`. Frontend: TypeScript.

## Global Constraints

- All Python commands use `uv run` (never `python` directly)
- Test runner: `uv run pytest tests/path/to/test.py::test_name -v`
- Full suite: `cd apps/backend && uv run pytest`
- All async tests require `@pytest.mark.asyncio`; `asyncio_mode = "auto"` is set in pyproject.toml
- Working directory for backend commands: `apps/backend/`
- Working directory for frontend commands: `apps/frontend/`
- Do not add `**kwargs` to tool functions — use signature inspection in `_run_tool` instead
- Commit after every task passes its tests

---

## Task 1: Fix tool_runner repo_path injection bug

**Files:**
- Modify: `apps/backend/src/main_graph/nodes/tool_runner.py`
- Modify: `apps/backend/tests/unit/nodes/test_tool_runner.py`

**Context:** `_run_tool` always calls `fn(repo_path=repo_path, **tc.args)`. External API tools like `github_advisory` and `osv_lookup` do not declare `repo_path` — this causes a silent `TypeError` caught by the except block, so these tools always return an error. Fix: inspect the function signature before injecting `repo_path`.

**Interfaces:**
- Produces: `_run_tool` calls `fn(**kwargs)` where `kwargs` only includes `repo_path` if `fn`'s signature declares it.

- [ ] **Step 1: Write the failing test**

Add to `apps/backend/tests/unit/nodes/test_tool_runner.py`:

```python
@pytest.mark.asyncio
async def test_tool_runner_does_not_inject_repo_path_for_tools_without_it():
    """Tools that don't declare repo_path should not receive it as a kwarg."""
    received_kwargs: dict = {}

    async def no_repo_tool(package_name: str) -> dict:
        received_kwargs["package_name"] = package_name
        return {"ok": True}

    tc = ToolCall(tool="no_repo_tool", args={"package_name": "lodash"}, reason="test")
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"no_repo_tool": no_repo_tool}):
        result = await tool_runner(_make_state([tc]), config={})
    assert result["tool_results"][0].error is None
    assert received_kwargs == {"package_name": "lodash"}


@pytest.mark.asyncio
async def test_tool_runner_injects_repo_path_for_tools_that_declare_it():
    """Tools that declare repo_path should receive it."""
    received_kwargs: dict = {}

    async def file_tool(repo_path: str, extra: str = "") -> dict:
        received_kwargs["repo_path"] = repo_path
        return {"ok": True}

    tc = ToolCall(tool="file_tool", args={}, reason="test")
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"file_tool": file_tool}):
        result = await tool_runner(_make_state([tc], repo_path="/tmp/myrepo"), config={})
    assert result["tool_results"][0].error is None
    assert received_kwargs["repo_path"] == "/tmp/myrepo"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_tool_runner.py::test_tool_runner_does_not_inject_repo_path_for_tools_without_it tests/unit/nodes/test_tool_runner.py::test_tool_runner_injects_repo_path_for_tools_that_declare_it -v
```

Expected: FAIL — `no_repo_tool` receives `repo_path` as unexpected kwarg → TypeError → error result.

- [ ] **Step 3: Fix `_run_tool` in `tool_runner.py`**

Add `import inspect` at the top (with other stdlib imports). Replace the `output = await fn(repo_path=repo_path, **tc.args)` line:

Full updated `_run_tool`:

```python
async def _run_tool(tc: ToolCall, repo_path: str) -> ToolResult:
    start = time.monotonic()
    fn = TOOL_REGISTRY.get(tc.tool)
    if fn is None:
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output={}, error=f"tool '{tc.tool}' not found in registry",
            duration_ms=0,
        )
    try:
        sig = inspect.signature(fn)
        kwargs = dict(tc.args)
        if "repo_path" in sig.parameters:
            kwargs["repo_path"] = repo_path
        output = await fn(**kwargs)
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output=output, error=None,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as exc:
        logger.warning("tool_runner: tool=%s failed: %s", tc.tool, exc)
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output={}, error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
```

The full import block at the top of `tool_runner.py` becomes:

```python
import asyncio
import inspect
import logging
import time
import uuid
```

- [ ] **Step 4: Run all tool_runner tests**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_tool_runner.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/nodes/tool_runner.py apps/backend/tests/unit/nodes/test_tool_runner.py
git commit -m "fix(tool-runner): only inject repo_path for tools that declare it"
```

---

## Task 2: Persist autopilot in job metadata

**Files:**
- Modify: `apps/backend/src/models/job.py`
- Modify: `apps/backend/src/api/routes.py`
- Modify: `apps/frontend/src/api/types.ts`
- Modify: `apps/backend/tests/unit/test_job.py`

**Context:** `autopilot` is already on `AnalysisRequest` and passed to `run_analysis`, but not stored in `JobMetadata`. Add it so a completed job's metadata shows how it ran.

**Interfaces:**
- Produces: `JobMetadata(repo_url=..., concern=..., autopilot=...)` — used by routes.py

- [ ] **Step 1: Write the failing tests**

Add to `apps/backend/tests/unit/test_job.py`:

```python
def test_job_metadata_stores_autopilot():
    job = Job(metadata=JobMetadata(repo_url=_REPO_URL, concern="security", autopilot=True))
    assert job.metadata.autopilot is True
    doc = job.to_doc()
    assert doc["metadata"]["autopilot"] is True


def test_job_metadata_autopilot_defaults_false():
    job = Job(metadata=JobMetadata(repo_url=_REPO_URL, concern="security"))
    assert job.metadata.autopilot is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/backend && uv run pytest tests/unit/test_job.py::test_job_metadata_stores_autopilot tests/unit/test_job.py::test_job_metadata_autopilot_defaults_false -v
```

Expected: FAIL — `JobMetadata` has no `autopilot` field.

- [ ] **Step 3: Add `autopilot` to `JobMetadata` in `models/job.py`**

```python
class JobMetadata(BaseModel):
    repo_url: str
    concern: str
    autopilot: bool = False
```

- [ ] **Step 4: Pass `autopilot` in `routes.py`**

In the `analyze` endpoint, change:

```python
job = Job(metadata=JobMetadata(repo_url=request.repo_url, concern=request.concern))
```

to:

```python
job = Job(metadata=JobMetadata(
    repo_url=request.repo_url,
    concern=request.concern,
    autopilot=request.autopilot,
))
```

- [ ] **Step 5: Run the job model tests**

```bash
cd apps/backend && uv run pytest tests/unit/test_job.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Update frontend type**

In `apps/frontend/src/api/types.ts`, change `JobMetadata`:

```typescript
export interface JobMetadata {
  repo_url: string
  concern: string
  autopilot: boolean
}
```

- [ ] **Step 7: Verify frontend builds**

```bash
cd apps/frontend && pnpm run build
```

Expected: no TypeScript errors.

- [ ] **Step 8: Commit**

```bash
git add apps/backend/src/models/job.py apps/backend/src/api/routes.py apps/backend/tests/unit/test_job.py apps/frontend/src/api/types.ts
git commit -m "feat(metadata): persist autopilot mode in job metadata"
```

---

## Task 3: Surface HITL conversation in conductor prompt

**Files:**
- Modify: `apps/backend/src/main_graph/nodes/conductor.py`
- Modify: `apps/backend/tests/unit/nodes/test_conductor.py`

**Context:** `hitl_gate.py` returns `{"messages": [AIMessage, HumanMessage]}` which accumulates in `state["messages"]`. The conductor prompt currently shows only `len(messages)` — the LLM never sees the conversation content. Fix: format actual message content in the prompt.

**Interfaces:**
- Produces: `_format_messages(messages: list) -> str` — exported for testing

- [ ] **Step 1: Write the failing tests**

Add to `apps/backend/tests/unit/nodes/test_conductor.py`:

```python
from langchain_core.messages import AIMessage, HumanMessage

from src.main_graph.nodes.conductor import _format_messages


def test_format_messages_returns_placeholder_when_empty():
    assert _format_messages([]) == "No conversation history."


def test_format_messages_labels_assistant_and_user():
    msgs = [AIMessage(content="Shall I proceed?"), HumanMessage(content="Yes, continue.")]
    result = _format_messages(msgs)
    assert "[assistant]: Shall I proceed?" in result
    assert "[user]: Yes, continue." in result


@pytest.mark.asyncio
async def test_conductor_includes_conversation_content_in_prompt():
    """When state has messages, conductor prompt includes their text, not just a count."""
    decision = ConductorDecision(
        tool_calls=[], findings=[], ask_user=None, checkpoint_message=None,
        finalize=True, reasoning="done",
    )
    msgs = [AIMessage(content="Proceed to report?"), HumanMessage(content="Yes please")]
    captured: list = []

    async def capture_invoke(messages, **_):
        captured.extend(messages)
        return decision

    with patch("src.main_graph.nodes.conductor._llm") as mock_llm:
        mock_llm.with_structured_output.return_value.ainvoke = capture_invoke
        await conductor(_make_state(messages=msgs), config={"configurable": {}})

    full_text = "\n".join(
        m["content"] if isinstance(m, dict) else str(m.content)
        for m in captured
    )
    assert "Proceed to report?" in full_text
    assert "Yes please" in full_text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_conductor.py::test_format_messages_returns_placeholder_when_empty tests/unit/nodes/test_conductor.py::test_format_messages_labels_assistant_and_user tests/unit/nodes/test_conductor.py::test_conductor_includes_conversation_content_in_prompt -v
```

Expected: FAIL — `_format_messages` not found.

- [ ] **Step 3: Add import and `_format_messages` to `conductor.py`**

Add to the import block at the top of `conductor.py`:

```python
from langchain_core.messages import AIMessage
```

Add `_format_messages` after `_format_findings`:

```python
def _format_messages(messages: list) -> str:
    if not messages:
        return "No conversation history."
    parts = []
    for m in messages:
        role = "assistant" if isinstance(m, AIMessage) else "user"
        parts.append(f"[{role}]: {m.content}")
    return "\n".join(parts)
```

- [ ] **Step 4: Update the prompt in `conductor`**

In the `conductor` async function, change the `user_prompt` line:

```python
f"Conversation history: {len(state.get('messages') or [])} messages\n\n"
```

to:

```python
f"Conversation history:\n{_format_messages(state.get('messages') or [])}\n\n"
```

- [ ] **Step 5: Run all conductor tests**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_conductor.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/nodes/conductor.py apps/backend/tests/unit/nodes/test_conductor.py
git commit -m "feat(conductor): surface HITL conversation history in LLM prompt"
```

---

## Task 4: EvidenceRef model — replace evidence_refs with inline evidence

**Files:**
- Modify: `apps/backend/src/models/conductor.py`
- Modify: `apps/backend/src/main_graph/nodes/conductor.py`
- Modify: `apps/backend/src/main_graph/nodes/report_builder.py`
- Modify: `apps/backend/tests/unit/models/test_conductor_models.py`
- Modify: `apps/backend/tests/unit/nodes/test_conductor.py`
- Modify: `apps/backend/tests/unit/nodes/test_report_builder.py`
- Modify: `apps/frontend/src/api/types.ts`

**Context:** `FindingNote.evidence_refs: list[str]` stores opaque tool-result UUIDs that are only in the LangGraph checkpoint. Replace with `evidence: list[EvidenceRef]` where each entry carries the tool name, a URL (if available), and a relevant log snippet — persisted directly in the report.

**Interfaces:**
- Produces:
  - `EvidenceRef(tool: str, url: str | None, log_snippet: str)` in `models/conductor.py`
  - `FindingNote.evidence: list[EvidenceRef]` replaces `FindingNote.evidence_refs: list[str]`

- [ ] **Step 1: Write the failing model test**

Check `apps/backend/tests/unit/models/test_conductor_models.py` — if it tests `FindingNote`, update it. Add:

```python
from src.models.conductor import EvidenceRef, FindingNote


def test_evidence_ref_fields():
    ev = EvidenceRef(tool="npm_audit", url="https://example.com/advisory", log_snippet="critical vuln found")
    assert ev.tool == "npm_audit"
    assert ev.url == "https://example.com/advisory"
    assert ev.log_snippet == "critical vuln found"


def test_evidence_ref_url_nullable():
    ev = EvidenceRef(tool="npm_list", url=None, log_snippet="lodash 4.17.21")
    assert ev.url is None


def test_finding_note_uses_evidence_not_evidence_refs():
    ev = EvidenceRef(tool="npm_audit", url=None, log_snippet="vuln")
    finding = FindingNote(dep_name="lodash", severity="high", description="outdated", evidence=[ev])
    assert len(finding.evidence) == 1
    assert finding.evidence[0].tool == "npm_audit"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/backend && uv run pytest tests/unit/models/test_conductor_models.py -v
```

Expected: FAIL — `EvidenceRef` not found, `FindingNote` has no `evidence` field.

- [ ] **Step 3: Update `models/conductor.py`**

Full new content:

```python
from pydantic import BaseModel


class ToolCall(BaseModel):
    tool: str
    args: dict
    reason: str


class EvidenceRef(BaseModel):
    tool: str
    url: str | None
    log_snippet: str


class FindingNote(BaseModel):
    dep_name: str
    severity: str  # "critical" | "high" | "medium" | "low" | "info"
    description: str
    evidence: list[EvidenceRef]


class ToolResult(BaseModel):
    id: str
    tool: str
    args: dict
    output: dict
    error: str | None
    duration_ms: int


class ConductorDecision(BaseModel):
    tool_calls: list[ToolCall]
    findings: list[FindingNote]
    ask_user: str | None
    checkpoint_message: str | None
    finalize: bool
    reasoning: str
```

- [ ] **Step 4: Run model tests**

```bash
cd apps/backend && uv run pytest tests/unit/models/ -v
```

Expected: PASS.

- [ ] **Step 5: Fix broken tests in test_conductor.py**

In `test_conductor.py`, find all `FindingNote(... evidence_refs=...)` and replace with `evidence=[]`:

```python
new_finding = FindingNote(dep_name="lodash", severity="high", description="vuln", evidence=[])
```

- [ ] **Step 6: Fix broken tests in test_report_builder.py**

In `test_report_builder.py`, update the `FindingNote` imports and usages. The file currently has:

```python
from src.models.conductor import FindingNote
```

Add `EvidenceRef` to the import:

```python
from src.models.conductor import EvidenceRef, FindingNote
```

Update findings in the test:

```python
findings = [
    FindingNote(dep_name="lodash", severity="high", description="vuln", evidence=[
        EvidenceRef(tool="npm_audit", url="https://example.com/cve-1", log_snippet="critical issue in lodash")
    ]),
    FindingNote(dep_name="express", severity="medium", description="outdated", evidence=[]),
]
```

- [ ] **Step 7: Update conductor system prompt for evidence**

In `conductor.py`, add to `_SYSTEM` the evidence instruction. The current `_SYSTEM` ends with `{tool_descriptions}`. Add the evidence rule before `Available tools`:

```python
_SYSTEM = """\
You are a dependency risk investigator. You run tools to investigate a Node.js project and accumulate findings.

Each iteration you MUST output a ConductorDecision with exactly one primary action:
1. finalize=true — you have enough findings to write the report (highest priority)
2. ask_user or checkpoint_message set — you need user input before continuing
3. tool_calls non-empty — run these tools in parallel and observe results next iteration

Rules:
- Never repeat a tool call with identical arguments.
- Emit FindingNote entries for every risk you observe in tool results.
- In autopilot mode, never set ask_user or checkpoint_message.
- After 10 iterations, you MUST finalize regardless of confidence.
- When emitting a FindingNote, populate the evidence list with one entry per supporting tool result. Set tool to the tool name, url to any advisory URL, CVE permalink, or OSV link present in the output (null if none), and log_snippet to the most relevant excerpt (max 400 characters).

Available tools:
{tool_descriptions}
"""
```

- [ ] **Step 8: Update `report_builder.py` — remove evidence_refs, pass evidence through**

The `_SYSTEM` prompt currently has `"evidence_refs": ["<tool result id>"]`. Replace the entire `_SYSTEM` string in `report_builder.py`:

```python
_SYSTEM = """\
You are a technical report writer. Given structured investigation findings, produce a JSON analysis report.

Output ONLY valid JSON matching this exact shape:
{
  "executive_summary": "<2-4 sentence summary of overall risk>",
  "overall_risk_level": "<critical|high|medium|low|none>",
  "findings": [
    {
      "dep_name": "<package name>",
      "severity": "<critical|high|medium|low|info>",
      "description": "<concise description>",
      "recommendation": "<actionable fix>",
      "evidence": [{"tool": "<tool>", "url": "<url or null>", "log_snippet": "<excerpt>"}]
    }
  ],
  "recommendations": ["<deduplicated list of top recommendations>"]
}
"""
```

Update `_format_findings` to include evidence:

```python
def _format_findings(findings: list[FindingNote]) -> str:
    return json.dumps(
        [
            {
                "dep_name": f.dep_name,
                "severity": f.severity,
                "description": f.description,
                "evidence": [e.model_dump() for e in f.evidence],
            }
            for f in findings
        ],
        indent=2,
    )
```

- [ ] **Step 9: Run all affected tests**

```bash
cd apps/backend && uv run pytest tests/unit/models/ tests/unit/nodes/test_conductor.py tests/unit/nodes/test_report_builder.py -v
```

Expected: all PASS.

- [ ] **Step 10: Update frontend types**

In `apps/frontend/src/api/types.ts`, add `EvidenceRef` and update the report finding type.

Add after the `Severity` type:

```typescript
export interface EvidenceRef {
  tool: string
  url: string | null
  log_snippet: string
}
```

Update `ReportFinding` — add `evidence` field:

```typescript
export interface ReportFinding {
  dep_name: string
  risk_score: number
  confidence: number
  severity: Severity
  summary: string
  recommendation: string | null
  alternatives: string[]
  supporting_evidence_count: number
  contradictions_count: number
  missing_evidence: string[]
  evidence?: EvidenceRef[]
}
```

- [ ] **Step 11: Verify frontend builds**

```bash
cd apps/frontend && pnpm run build
```

Expected: no TypeScript errors.

- [ ] **Step 12: Run full backend suite**

```bash
cd apps/backend && uv run pytest
```

Expected: all tests PASS.

- [ ] **Step 13: Commit**

```bash
git add apps/backend/src/models/conductor.py \
        apps/backend/src/main_graph/nodes/conductor.py \
        apps/backend/src/main_graph/nodes/report_builder.py \
        apps/backend/tests/unit/models/test_conductor_models.py \
        apps/backend/tests/unit/nodes/test_conductor.py \
        apps/backend/tests/unit/nodes/test_report_builder.py \
        apps/frontend/src/api/types.ts
git commit -m "feat(evidence): replace opaque evidence_refs with inline EvidenceRef objects"
```

---

## Task 5: Add `resolve_transitive_parent` tool

**Files:**
- Modify: `apps/backend/src/main_graph/tools/npm_cli.py`
- Modify: `apps/backend/tests/unit/tools/test_npm_cli.py`

**Context:** The conductor needs to know whether a finding's package is a direct dependency (in package.json) or a transitive one. This tool runs `npm ls --json --all`, compares to `package.json` direct deps, and returns parent chain info. Lives in `npm_cli.py` because it uses the same subprocess helpers (`_run_npm`, `_safe_json`).

**Interfaces:**
- Produces: `resolve_transitive_parent(repo_path, package_name)` → `{package, is_direct, brought_in_by, dep_chain}`

- [ ] **Step 1: Write failing tests**

Add to `apps/backend/tests/unit/tools/test_npm_cli.py`:

```python
import json
import os

import pytest


@pytest.fixture
def repo_with_pkg(tmp_path):
    pkg = {
        "name": "my-app",
        "dependencies": {"express": "^4.18.0"},
        "devDependencies": {"jest": "^29.0.0"},
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    return str(tmp_path)


@pytest.mark.asyncio
async def test_resolve_transitive_parent_direct_dep(repo_with_pkg):
    """express is a direct dep — is_direct should be True."""
    from unittest.mock import AsyncMock, patch

    # npm ls won't be called when dep is direct
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(return_value=("{}", ""))):
        result = await TOOL_REGISTRY["resolve_transitive_parent"](
            repo_path=repo_with_pkg, package_name="express"
        )
    assert result["is_direct"] is True
    assert result["brought_in_by"] == []


@pytest.mark.asyncio
async def test_resolve_transitive_parent_transitive_dep(repo_with_pkg):
    """accepts is a transitive dep brought in by express."""
    from unittest.mock import AsyncMock, patch

    npm_tree = json.dumps({
        "name": "my-app",
        "dependencies": {
            "express": {
                "version": "4.18.2",
                "dependencies": {
                    "accepts": {"version": "1.3.8", "dependencies": {}}
                }
            }
        }
    })
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(return_value=(npm_tree, ""))):
        result = await TOOL_REGISTRY["resolve_transitive_parent"](
            repo_path=repo_with_pkg, package_name="accepts"
        )
    assert result["is_direct"] is False
    assert "express" in result["brought_in_by"]


@pytest.mark.asyncio
async def test_resolve_transitive_parent_unknown_package(repo_with_pkg):
    """Package not found anywhere returns empty parents."""
    from unittest.mock import AsyncMock, patch

    npm_tree = json.dumps({"name": "my-app", "dependencies": {}})
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(return_value=(npm_tree, ""))):
        result = await TOOL_REGISTRY["resolve_transitive_parent"](
            repo_path=repo_with_pkg, package_name="ghost-package"
        )
    assert result["is_direct"] is False
    assert result["brought_in_by"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/backend && uv run pytest tests/unit/tools/test_npm_cli.py::test_resolve_transitive_parent_direct_dep tests/unit/tools/test_npm_cli.py::test_resolve_transitive_parent_transitive_dep tests/unit/tools/test_npm_cli.py::test_resolve_transitive_parent_unknown_package -v
```

Expected: FAIL — tool not registered.

- [ ] **Step 3: Implement helpers and the tool in `npm_cli.py`**

Add `import os` to the existing imports (after `import json`).

Add these helpers and the tool at the bottom of `npm_cli.py`:

```python
def _in_subtree(deps: dict, target: str) -> bool:
    """Return True if target package exists anywhere in the deps subtree."""
    if target in deps:
        return True
    return any(_in_subtree(info.get("dependencies") or {}, target) for info in deps.values())


def _find_chain(deps: dict, target: str, prefix: str = "") -> str:
    """Return first dep_chain string that reaches target, or 'unknown'."""
    for name, info in deps.items():
        current = f"{prefix} → {name}" if prefix else name
        sub = info.get("dependencies") or {}
        if target in sub:
            return f"{current} → {target}"
        result = _find_chain(sub, target, current)
        if result != "unknown":
            return result
    return "unknown"


@register(
    "resolve_transitive_parent",
    "Determines if a package is a direct or transitive dependency and identifies which direct deps bring it in",
)
async def resolve_transitive_parent(repo_path: str, package_name: str) -> dict:
    try:
        pkg_path = os.path.join(repo_path, "package.json")
        with open(pkg_path) as f:
            pkg = json.load(f)
        direct_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

        if package_name in direct_deps:
            return {
                "package": package_name,
                "is_direct": True,
                "brought_in_by": [],
                "dep_chain": package_name,
            }

        stdout, _ = await _run_npm(["ls", "--json", "--all"], repo_path)
        tree = _safe_json(stdout)
        tree_deps = tree.get("dependencies") or {}

        parents = [name for name, info in tree_deps.items()
                   if _in_subtree(info.get("dependencies") or {}, package_name)]

        return {
            "package": package_name,
            "is_direct": False,
            "brought_in_by": parents,
            "dep_chain": _find_chain(tree_deps, package_name),
        }
    except Exception as exc:
        logger.warning("resolve_transitive_parent failed: %s", exc)
        return {"error": str(exc), "package": package_name}
```

- [ ] **Step 4: Run all npm_cli tests**

```bash
cd apps/backend && uv run pytest tests/unit/tools/test_npm_cli.py -v
```

Expected: all tests PASS (including old npm_list/npm_audit/npm_outdated tests).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/tools/npm_cli.py apps/backend/tests/unit/tools/test_npm_cli.py
git commit -m "feat(tools): add resolve_transitive_parent tool"
```

---

## Task 6: Add `web_search` tool + conductor transitive dep prompt

**Files:**
- Modify: `apps/backend/src/utils/config.py`
- Modify: `apps/backend/src/main_graph/tools/external_api.py`
- Modify: `apps/backend/src/main_graph/nodes/conductor.py`
- Create: `apps/backend/tests/unit/tools/test_web_search.py`

**Context:** Adds a Tavily-backed web search tool for the conductor to look up package alternatives. Also updates the conductor's system prompt so it knows how to handle transitive deps: call `resolve_transitive_parent`, recommend updating the parent direct dep, and use `web_search` to find alternatives when no safe parent version exists.

**Important:** This task modifies `conductor.py` again (specifically `_SYSTEM`). Apply it AFTER Task 4's version is in place.

**Interfaces:**
- Produces: `web_search(query: str)` → `{query, results: [{title, url, snippet}]}`
- Consumes: `settings.tavily_api_key` from `utils/config.py`

- [ ] **Step 1: Add Tavily key to settings**

In `apps/backend/src/utils/config.py`, add inside `Settings`:

```python
# Tavily (web search for conductor)
tavily_api_key: str = ""
```

- [ ] **Step 2: Write the failing web_search tests**

Create `apps/backend/tests/unit/tools/test_web_search.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.main_graph.tools.external_api  # trigger registration
from src.main_graph.tools.registry import TOOL_REGISTRY


@pytest.mark.asyncio
async def test_web_search_returns_results():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "results": [
            {"title": "lodash alternative", "url": "https://example.com", "content": "Use ramda instead"}
        ]
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=fake_response)

    with (
        patch("src.main_graph.tools.external_api.settings") as mock_settings,
        patch("src.main_graph.tools.external_api.httpx.AsyncClient", return_value=mock_client),
    ):
        mock_settings.tavily_api_key = "test-key"
        result = await TOOL_REGISTRY["web_search"](query="lodash alternatives npm")

    assert result["query"] == "lodash alternatives npm"
    assert len(result["results"]) == 1
    assert result["results"][0]["url"] == "https://example.com"


@pytest.mark.asyncio
async def test_web_search_returns_error_when_no_api_key():
    with patch("src.main_graph.tools.external_api.settings") as mock_settings:
        mock_settings.tavily_api_key = ""
        result = await TOOL_REGISTRY["web_search"](query="test")
    assert "error" in result
    assert result["results"] == []


@pytest.mark.asyncio
async def test_web_search_handles_http_error():
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

    with (
        patch("src.main_graph.tools.external_api.settings") as mock_settings,
        patch("src.main_graph.tools.external_api.httpx.AsyncClient", return_value=mock_client),
    ):
        mock_settings.tavily_api_key = "test-key"
        result = await TOOL_REGISTRY["web_search"](query="test")

    assert "error" in result
    assert result["results"] == []


def test_web_search_is_registered():
    assert "web_search" in TOOL_REGISTRY
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd apps/backend && uv run pytest tests/unit/tools/test_web_search.py -v
```

Expected: FAIL — `web_search` not in registry.

- [ ] **Step 4: Add `web_search` to `external_api.py`**

Add at the top of `external_api.py` (with existing imports):

```python
from src.utils.config import settings
```

Add at the bottom of `external_api.py`:

```python
@register("web_search", "Searches the web for package alternatives, security advisories, or migration guides")
async def web_search(query: str) -> dict:
    if not settings.tavily_api_key:
        return {"error": "TAVILY_API_KEY not configured", "results": []}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": settings.tavily_api_key, "query": query, "max_results": 5},
            )
            r.raise_for_status()
            data = r.json()
        results = [
            {"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("content", "")}
            for item in data.get("results", [])
        ]
        return {"query": query, "results": results}
    except Exception as exc:
        return {"error": str(exc), "results": []}
```

- [ ] **Step 5: Run web_search tests**

```bash
cd apps/backend && uv run pytest tests/unit/tools/test_web_search.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 6: Update conductor `_SYSTEM` for transitive dep awareness**

In `conductor.py`, replace the `_SYSTEM` string (which after Task 4 now includes the evidence rule). Add the transitive dep rules after the evidence rule:

```python
_SYSTEM = """\
You are a dependency risk investigator. You run tools to investigate a Node.js project and accumulate findings.

Each iteration you MUST output a ConductorDecision with exactly one primary action:
1. finalize=true — you have enough findings to write the report (highest priority)
2. ask_user or checkpoint_message set — you need user input before continuing
3. tool_calls non-empty — run these tools in parallel and observe results next iteration

Rules:
- Never repeat a tool call with identical arguments.
- Emit FindingNote entries for every risk you observe in tool results.
- In autopilot mode, never set ask_user or checkpoint_message.
- After 10 iterations, you MUST finalize regardless of confidence.
- When emitting a FindingNote, populate the evidence list with one entry per supporting tool result. Set tool to the tool name, url to any advisory URL, CVE permalink, or OSV link present in the output (null if none), and log_snippet to the most relevant excerpt (max 400 characters).
- For any finding involving a transitive dependency (a package NOT listed in package.json dependencies or devDependencies): call resolve_transitive_parent to identify which direct dep brings it in, then recommend updating that direct dep. Never recommend updating a transitive dep directly.
- If no safe version of the responsible direct dep exists, call web_search to find an alternative package and include the alternative in your recommendation.

Available tools:
{tool_descriptions}
"""
```

- [ ] **Step 7: Run all conductor tests**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_conductor.py -v
```

Expected: all PASS.

- [ ] **Step 8: Run full backend test suite**

```bash
cd apps/backend && uv run pytest
```

Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add apps/backend/src/utils/config.py \
        apps/backend/src/main_graph/tools/external_api.py \
        apps/backend/src/main_graph/nodes/conductor.py \
        apps/backend/tests/unit/tools/test_web_search.py
git commit -m "feat(tools): add web_search tool; update conductor for transitive dep awareness"
```

---

## Task 7: Dependency tree persistence + API endpoint

**Files:**
- Modify: `apps/backend/src/domain/ports/job_repository_port.py`
- Modify: `apps/backend/src/services/job_dao.py`
- Modify: `apps/backend/src/services/job_runner.py`
- Modify: `apps/backend/src/api/routes.py`
- Modify: `apps/backend/src/api/schemas.py`
- Modify: `apps/backend/tests/unit/test_job.py`
- Modify: `apps/backend/tests/unit/services/test_job_runner.py`

**Context:** When `tool_runner` node runs `npm_list`, save its output to a `dep_trees` MongoDB collection keyed by `job_id`. Expose it via `GET /analyze/{trace_id}/dep-tree` for future frontend visualization.

**Interfaces:**
- Produces:
  - `dao.save_dep_tree(job_id: str, tree: dict) -> None`
  - `dao.get_dep_tree(job_id: str) -> dict | None`
  - `GET /analyze/{trace_id}/dep-tree` → `DepTreeResponse`

- [ ] **Step 1: Write failing tests**

Add to `apps/backend/tests/unit/test_job.py`:

```python
def test_job_dao_implements_save_dep_tree():
    import inspect
    from src.domain.ports.job_repository_port import JobRepositoryPort
    from src.services.job_dao import JobDAO

    assert hasattr(JobDAO, "save_dep_tree")
    assert inspect.iscoroutinefunction(JobDAO.save_dep_tree)
    assert "save_dep_tree" in {m for m in dir(JobRepositoryPort) if not m.startswith("_")}


def test_job_dao_implements_get_dep_tree():
    import inspect
    from src.domain.ports.job_repository_port import JobRepositoryPort
    from src.services.job_dao import JobDAO

    assert hasattr(JobDAO, "get_dep_tree")
    assert inspect.iscoroutinefunction(JobDAO.get_dep_tree)
    assert "get_dep_tree" in {m for m in dir(JobRepositoryPort) if not m.startswith("_")}
```

Add to `apps/backend/tests/unit/services/test_job_runner.py`:

```python
@pytest.mark.asyncio
async def test_stream_graph_saves_dep_tree_when_npm_list_succeeds():
    """When TOOL_RUNNER emits an npm_list result with no error, dep tree is saved."""
    dao = _make_dao()
    job_id = "job-deptree"

    npm_tree = {"name": "my-app", "dependencies": {"lodash": {"version": "4.17.21"}}}
    tool_result = MagicMock()
    tool_result.tool = "npm_list"
    tool_result.error = None
    tool_result.output = npm_tree

    async def tree_stream(*args, **kwargs):
        yield {TOOL_RUNNER: {"tool_results": [tool_result]}}

    mock_graph = MagicMock()
    mock_graph.astream = tree_stream

    await _stream_graph(mock_graph, {}, {}, dao, job_id)

    dao.save_dep_tree.assert_awaited_once_with(job_id, npm_tree)


@pytest.mark.asyncio
async def test_stream_graph_skips_dep_tree_when_npm_list_errors():
    """When npm_list result has an error, dep tree is not saved."""
    dao = _make_dao()
    job_id = "job-deptree-err"

    tool_result = MagicMock()
    tool_result.tool = "npm_list"
    tool_result.error = "npm command failed"
    tool_result.output = {}

    async def error_stream(*args, **kwargs):
        yield {TOOL_RUNNER: {"tool_results": [tool_result]}}

    mock_graph = MagicMock()
    mock_graph.astream = error_stream

    await _stream_graph(mock_graph, {}, {}, dao, job_id)

    dao.save_dep_tree.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/backend && uv run pytest tests/unit/test_job.py::test_job_dao_implements_save_dep_tree tests/unit/test_job.py::test_job_dao_implements_get_dep_tree tests/unit/services/test_job_runner.py::test_stream_graph_saves_dep_tree_when_npm_list_succeeds tests/unit/services/test_job_runner.py::test_stream_graph_skips_dep_tree_when_npm_list_errors -v
```

Expected: FAIL.

- [ ] **Step 3: Add abstract methods to `job_repository_port.py`**

Add at the end of `JobRepositoryPort` (before the closing):

```python
    @abstractmethod
    async def save_dep_tree(self, job_id: str, tree: dict) -> None: ...

    @abstractmethod
    async def get_dep_tree(self, job_id: str) -> dict | None: ...
```

- [ ] **Step 4: Implement in `job_dao.py`**

In `JobDAO.__init__`, add the second collection:

```python
def __init__(self):
    self._col = get_db()["jobs"]
    self._dep_trees_col = get_db()["dep_trees"]
```

Add the two methods to `JobDAO` (before `get_pending`):

```python
async def save_dep_tree(self, job_id: str, tree: dict) -> None:
    await self._dep_trees_col.replace_one(
        {"_id": job_id},
        {"_id": job_id, "created_at": datetime.now(UTC), "tree": tree},
        upsert=True,
    )

async def get_dep_tree(self, job_id: str) -> dict | None:
    doc = await self._dep_trees_col.find_one({"_id": job_id})
    return doc["tree"] if doc else None
```

- [ ] **Step 5: Call `save_dep_tree` in `job_runner.py`**

In `_stream_graph`, inside the `elif node_name == TOOL_RUNNER:` block, after the `push_artifact_item` call, add:

```python
            for tr in results:
                if tr.tool == "npm_list" and not tr.error:
                    await dao.save_dep_tree(job_id, tr.output)
```

The full TOOL_RUNNER handler now looks like:

```python
            elif node_name == TOOL_RUNNER:
                await dao.start_artifact(job_id, TOOL_RUNNER)
                results = node_update.get("tool_results") or []
                await dao.push_artifact_item(job_id, TOOL_RUNNER, "iterations", {
                    "conductor_iteration": current_conductor_iteration,
                    "tools_run": [tr.tool for tr in results],
                    "errors": [{"tool": tr.tool, "error": tr.error} for tr in results if tr.error],
                    "started_at": datetime.now(UTC).isoformat(),
                })
                for tr in results:
                    if tr.tool == "npm_list" and not tr.error:
                        await dao.save_dep_tree(job_id, tr.output)
```

- [ ] **Step 6: Run the new tests**

```bash
cd apps/backend && uv run pytest tests/unit/test_job.py tests/unit/services/test_job_runner.py -v
```

Expected: all PASS.

- [ ] **Step 7: Add `DepTreeResponse` to `schemas.py`**

Add to `apps/backend/src/api/schemas.py`:

```python
class DepTreeResponse(BaseModel):
    job_id: str
    tree: dict
```

- [ ] **Step 8: Add the dep-tree endpoint to `routes.py`**

Add the import for `DepTreeResponse` in `routes.py`:

```python
from src.api.schemas import (
    AnalysisRequest,
    AnalysisStatusResponse,
    ChatRequest,
    DepTreeResponse,
    JobListItem,
    JobsListResponse,
)
```

Add the endpoint after the `get_analysis_status` endpoint:

```python
@router.get("/analyze/{trace_id}/dep-tree", response_model=DepTreeResponse)
async def get_dep_tree(
    trace_id: str,
    dao: JobRepositoryPort = Depends(get_job_repo),
):
    tree = await dao.get_dep_tree(trace_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="dependency tree not yet available")
    return DepTreeResponse(job_id=trace_id, tree=tree)
```

- [ ] **Step 9: Run full backend suite**

```bash
cd apps/backend && uv run pytest
```

Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add apps/backend/src/domain/ports/job_repository_port.py \
        apps/backend/src/services/job_dao.py \
        apps/backend/src/services/job_runner.py \
        apps/backend/src/api/routes.py \
        apps/backend/src/api/schemas.py \
        apps/backend/tests/unit/test_job.py \
        apps/backend/tests/unit/services/test_job_runner.py
git commit -m "feat(dep-tree): persist npm dependency tree; add GET /analyze/{id}/dep-tree endpoint"
```
