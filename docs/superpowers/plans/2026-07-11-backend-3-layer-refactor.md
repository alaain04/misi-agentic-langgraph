# Backend 3-Layer Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the main graph from a flat 5-node ReAct loop into three explicit subgraphs (Preparation, Analysis, Report) that persist their outputs to MongoDB and communicate via result IDs.

**Architecture:** Three compiled LangGraph subgraphs chained in the main graph. MongoDB is the inter-layer data bus — each subgraph writes a result document and returns only its ID through `MainState`. Analysis dispatches domain-specific ReAct subagents in parallel via `Send()`. Report enriches findings with web search and vector-store code impact analysis.

**Tech Stack:** Python 3.12, LangGraph, FastAPI, MongoDB (motor/pymongo async), LangChain OpenAI (ChatOpenAI + OpenAIEmbeddings), pytest-asyncio, uv.

## Global Constraints

- Always `uv run pytest`, never `python -m pytest`
- Always `uv add`, never `pip install`
- All async DB calls use `motor` (already in deps)
- All LLM calls use `get_llm(Model.GPT_5_4_MINI)` from `src/utils/llm.py`
- Embeddings use `langchain_openai.OpenAIEmbeddings` (already in deps via langchain-openai)
- No HITL gates in this refactor — remove `ask_user` / `checkpoint_message` paths
- `FindingNote` in `src/models/conductor.py` is the canonical finding type — reuse it everywhere
- Tests live in `tests/unit/` (pure), `tests/integration/` (requires MongoDB)
- Follow existing import style: `from __future__ import annotations` at top of every new file

---

## File Map

### New files
| File | Responsibility |
|---|---|
| `src/models/results.py` | PrepResult, EvidenceBundle, AnalysisResult, ReportFinding, ReportResult, AgentDispatch, AnalysisConductorDecision, DomainAgentDecision, ReportConductorDecision |
| `src/db/result_dao.py` | ResultDAO — save/load for all result types |
| `src/main_graph/subgraphs/discovery/nodes/index_repository.py` | Walk + embed repo source files into InMemoryVectorStore, persist chunks to MongoDB |
| `src/main_graph/subgraphs/discovery/nodes/save_prep_result.py` | Assemble PrepResult from DiscoveryState and persist via ResultDAO |
| `src/main_graph/tools/search_code.py` | Factory `make_search_code_tool(vector_store_id)` → LangChain tool |
| `src/main_graph/tools/code_impact.py` | Factory `make_code_impact_tool(vector_store_id)` → LangChain tool |
| `src/main_graph/subgraphs/analysis/__init__.py` | exports `analysis_subgraph` |
| `src/main_graph/subgraphs/analysis/state.py` | AnalysisState TypedDict |
| `src/main_graph/subgraphs/analysis/graph.py` | `build_analysis_subgraph()` |
| `src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py` | ReAct conductor — reads PrepResult, emits AgentDispatch list |
| `src/main_graph/subgraphs/analysis/nodes/agent_dispatcher.py` | Deterministic fan-out — returns `list[Send]` |
| `src/main_graph/subgraphs/analysis/nodes/domain_agent.py` | Runs ReAct loop for one AgentDispatch; saves EvidenceBundle to MongoDB |
| `src/main_graph/subgraphs/analysis/nodes/evidence_collector.py` | No-op fan-in — triggers conductor re-entry |
| `src/main_graph/subgraphs/analysis/nodes/save_analysis_result.py` | Assembles AnalysisResult from bundle_ids and saves to MongoDB |
| `src/main_graph/subgraphs/analysis/agents/base_agent.py` | `run_react_loop(dispatch, prep, tools) → EvidenceBundle` |
| `src/main_graph/subgraphs/analysis/agents/registry.py` | `AGENT_REGISTRY: dict[str, list[str]]` — domain → tool name list |
| `src/main_graph/subgraphs/report/__init__.py` | exports `report_subgraph` |
| `src/main_graph/subgraphs/report/state.py` | ReportState TypedDict |
| `src/main_graph/subgraphs/report/graph.py` | `build_report_subgraph()` |
| `src/main_graph/subgraphs/report/nodes/report_conductor.py` | ReAct conductor — enriches findings, produces ReportResult |
| `src/main_graph/subgraphs/report/nodes/report_tool_runner.py` | Parallel tool executor for report conductor |
| `src/main_graph/subgraphs/report/nodes/save_report_result.py` | Persists ReportResult to MongoDB |

### Modified files
| File | Change |
|---|---|
| `src/models/conductor.py` | Remove `ask_user`, `checkpoint_message` from `ConductorDecision`; keep for now as legacy (old conductor is deleted anyway) |
| `src/main_graph/subgraphs/discovery/state.py` | Add `vector_store_id`, `prep_result_id` output fields |
| `src/main_graph/subgraphs/discovery/constants.py` | Add `INDEX_REPO`, `SAVE_PREP_RESULT` |
| `src/main_graph/subgraphs/discovery/nodes/__init__.py` | Export `index_repository`, `save_prep_result` |
| `src/main_graph/subgraphs/discovery/graph.py` | Wire `index_repository` → `generate_sbom`, add `save_prep_result` as final node |
| `src/main_graph/state.py` | Slim to: inputs + `prep_result_id`, `analysis_result_id`, `report_result_id`, `cancelled`, `discovery_error` |
| `src/main_graph/constants.py` | Replace old node constants with `PREP`, `ANALYSIS`, `REPORT` |
| `src/main_graph/graph.py` | 3-node chain: `PREP → ANALYSIS → REPORT` |
| `src/services/dependencies.py` | Add `get_result_dao()` singleton |
| `src/services/job_runner.py` | Update artifact tracking to new node names; update `_finalize` to use result IDs |
| `tests/unit/test_graph_routing.py` | Replace old routing tests with new 3-node routing tests |

---

## Task 1: Result models and ResultDAO

**Files:**
- Create: `src/models/results.py`
- Create: `src/db/result_dao.py`
- Modify: `src/services/dependencies.py`
- Test: `tests/unit/test_result_models.py`

**Interfaces:**
- Produces: `PrepResult`, `EvidenceBundle`, `AnalysisResult`, `ReportFinding`, `ReportResult`, `AgentDispatch`, `AnalysisConductorDecision`, `DomainAgentDecision`, `ReportConductorDecision` — all importable from `src/models/results.py`
- Produces: `ResultDAO` with `save_prep/get_prep`, `save_bundle/get_bundles`, `save_analysis/get_analysis`, `save_report/get_report` — importable from `src/db/result_dao.py`
- Produces: `get_result_dao()` from `src/services/dependencies.py`

- [ ] **Step 1: Write failing model tests**

```python
# tests/unit/test_result_models.py
from src.models.results import (
    PrepResult, AgentDispatch, AnalysisConductorDecision,
    EvidenceBundle, AnalysisResult, ReportFinding, ReportResult,
    DomainAgentDecision,
)
from src.models.conductor import FindingNote, EvidenceRef


def _finding() -> FindingNote:
    return FindingNote(dep_name="express", severity="high", description="CVE", evidence=[])


def test_prep_result_auto_id_and_timestamp():
    r = PrepResult(
        job_id="j1", repo_path="/tmp/r", project_metadata={},
        manifest_files=["package.json"], detected_package_manager="npm",
        dependency_graph={"direct": {}, "transitive": {}},
        sbom_cyclonedx={}, discovery_summary="summary", vector_store_id="vs1",
    )
    assert r.id
    assert r.created_at


def test_evidence_bundle_round_trip():
    b = EvidenceBundle(
        domain="vulnerabilities", hypothesis="h",
        findings=[_finding()], summary="s", confidence=0.9,
    )
    data = b.model_dump()
    b2 = EvidenceBundle(**data)
    assert b2.id == b.id
    assert b2.findings[0].dep_name == "express"


def test_analysis_conductor_decision_finalize():
    d = AnalysisConductorDecision(dispatches=[], finalize=True, reasoning="done")
    assert d.finalize
    assert d.dispatches == []


def test_agent_dispatch():
    d = AgentDispatch(
        domain="vulnerabilities", hypothesis="check CVEs",
        packages_to_focus=["express"], agent_type="vulnerability_agent",
    )
    assert d.agent_type == "vulnerability_agent"


def test_domain_agent_decision():
    d = DomainAgentDecision(
        tool_calls=[], findings=[_finding()],
        summary="found 1 CVE", confidence=0.85, finalize=True, reasoning="r",
    )
    assert d.confidence == 0.85


def test_report_result_round_trip():
    r = ReportResult(
        job_id="j1", concern="outdated deps",
        executive_summary="2 high risks found",
        overall_risk_level="high",
        findings=[ReportFinding(dep_name="express", severity="high", description="CVE",
                                recommendation="upgrade", alternatives=["fastify"],
                                affected_files=["src/server.ts:3"])],
        recommendations=["Upgrade express"],
    )
    assert r.id
    assert r.findings[0].alternatives == ["fastify"]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_result_models.py -v
```
Expected: `ModuleNotFoundError: No module named 'src.models.results'`

- [ ] **Step 3: Create `src/models/results.py`**

```python
from __future__ import annotations
import uuid
from datetime import UTC, datetime
from pydantic import BaseModel, Field
from src.models.conductor import FindingNote, ToolCall, ToolResult


class PrepResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    repo_path: str
    project_metadata: dict
    manifest_files: list[str]
    detected_package_manager: str
    dependency_graph: dict  # {"direct": {pkg: ver}, "transitive": {pkg: {version, brought_in_by}}}
    sbom_cyclonedx: dict
    discovery_summary: str
    vector_store_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class AgentDispatch(BaseModel):
    domain: str
    hypothesis: str
    packages_to_focus: list[str]
    agent_type: str  # key in AGENT_REGISTRY or "web_research" for fallback


class AnalysisConductorDecision(BaseModel):
    dispatches: list[AgentDispatch]
    finalize: bool = False
    reasoning: str


class DomainAgentDecision(BaseModel):
    tool_calls: list[ToolCall]
    findings: list[FindingNote]
    summary: str
    confidence: float
    finalize: bool
    reasoning: str


class EvidenceBundle(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    domain: str
    hypothesis: str
    findings: list[FindingNote]
    summary: str
    confidence: float


class AnalysisResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    concern: str
    findings: list[FindingNote]
    evidence_bundle_ids: list[str]
    iteration_count: int
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ReportFinding(BaseModel):
    dep_name: str
    severity: str
    description: str
    recommendation: str
    alternatives: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    evidence: list = Field(default_factory=list)


class ReportConductorDecision(BaseModel):
    tool_calls: list[ToolCall]
    finalize: bool = False
    reasoning: str


class ReportResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    concern: str
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    executive_summary: str
    overall_risk_level: str  # critical|high|medium|low|none
    findings: list[ReportFinding]
    recommendations: list[str]
```

- [ ] **Step 4: Create `src/db/result_dao.py`**

```python
from __future__ import annotations
from src.db.connection import get_db
from src.models.results import PrepResult, EvidenceBundle, AnalysisResult, ReportResult


class ResultDAO:
    def __init__(self) -> None:
        db = get_db()
        self._prep = db["prep_results"]
        self._bundles = db["evidence_bundles"]
        self._analysis = db["analysis_results"]
        self._report = db["report_results"]

    async def save_prep(self, result: PrepResult) -> str:
        await self._prep.insert_one(result.model_dump())
        return result.id

    async def get_prep(self, result_id: str) -> PrepResult:
        doc = await self._prep.find_one({"id": result_id}, {"_id": 0})
        return PrepResult(**doc)

    async def save_bundle(self, bundle: EvidenceBundle) -> str:
        await self._bundles.insert_one(bundle.model_dump())
        return bundle.id

    async def get_bundles(self, ids: list[str]) -> list[EvidenceBundle]:
        cursor = self._bundles.find({"id": {"$in": ids}}, {"_id": 0})
        return [EvidenceBundle(**doc) async for doc in cursor]

    async def save_analysis(self, result: AnalysisResult) -> str:
        await self._analysis.insert_one(result.model_dump())
        return result.id

    async def get_analysis(self, result_id: str) -> AnalysisResult:
        doc = await self._analysis.find_one({"id": result_id}, {"_id": 0})
        return AnalysisResult(**doc)

    async def save_report(self, result: ReportResult) -> str:
        await self._report.insert_one(result.model_dump())
        return result.id

    async def get_report(self, result_id: str) -> ReportResult:
        doc = await self._report.find_one({"id": result_id}, {"_id": 0})
        return ReportResult(**doc)
```

- [ ] **Step 5: Add `get_result_dao()` to `src/services/dependencies.py`**

```python
from functools import lru_cache
from src.domain.ports.job_repository_port import JobRepositoryPort
from src.services.job_dao import JobDAO
from src.db.result_dao import ResultDAO


@lru_cache(maxsize=1)
def get_job_repo() -> JobRepositoryPort:
    return JobDAO()


@lru_cache(maxsize=1)
def get_result_dao() -> ResultDAO:
    return ResultDAO()
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
uv run pytest tests/unit/test_result_models.py -v
```
Expected: all 7 tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/models/results.py src/db/result_dao.py src/services/dependencies.py tests/unit/test_result_models.py
git commit -m "feat: result models and DAO for 3-layer inter-layer persistence"
```

---

## Task 2: Repo indexer node and search_code / code_impact tools

**Files:**
- Create: `src/main_graph/subgraphs/discovery/nodes/index_repository.py`
- Create: `src/main_graph/tools/search_code.py`
- Create: `src/main_graph/tools/code_impact.py`
- Test: `tests/unit/tools/test_search_code.py`

**Interfaces:**
- Consumes: `DiscoveryState` with `repo_path: str`
- Produces: node `index_repository(state) -> {"vector_store_id": str}`
- Produces: `make_search_code_tool(vector_store_id: str) -> BaseTool` — tool name `search_code`, args `(query: str, top_k: int = 10)`
- Produces: `make_code_impact_tool(vector_store_id: str) -> BaseTool` — tool name `code_impact`, args `(package_name: str)`
- Produces: `get_vector_store(vector_store_id: str) -> InMemoryVectorStore | None` — for tests

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/tools/test_search_code.py
import asyncio
import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def test_make_search_code_tool_returns_tool():
    from src.main_graph.tools.search_code import make_search_code_tool
    tool = make_search_code_tool("vs-test")
    assert tool.name == "search_code"


def test_make_code_impact_tool_returns_tool():
    from src.main_graph.tools.code_impact import make_code_impact_tool
    tool = make_code_impact_tool("vs-test")
    assert tool.name == "code_impact"


@pytest.mark.asyncio
async def test_index_repository_writes_vector_store_id():
    from src.main_graph.subgraphs.discovery.nodes.index_repository import index_repository
    from src.main_graph.tools.search_code import get_vector_store

    with tempfile.TemporaryDirectory() as tmpdir:
        # create a fake source file
        (open(os.path.join(tmpdir, "index.ts"), "w")).write(
            'import express from "express";\nconst app = express();'
        )
        mock_embeddings = MagicMock()
        mock_embeddings.aembed_documents = AsyncMock(return_value=[[0.1, 0.2, 0.3]])

        with patch("src.main_graph.subgraphs.discovery.nodes.index_repository._embeddings", mock_embeddings):
            result = await index_repository({"repo_path": tmpdir, "job_id": "j1"})

    assert "vector_store_id" in result
    store = get_vector_store(result["vector_store_id"])
    assert store is not None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/tools/test_search_code.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `src/main_graph/tools/search_code.py`**

```python
from __future__ import annotations
import uuid
import logging
from langchain_core.tools import tool
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings

logger = logging.getLogger(__name__)

_embeddings = OpenAIEmbeddings()
_store_cache: dict[str, InMemoryVectorStore] = {}

_SOURCE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".mts", ".cts"}


def get_vector_store(vector_store_id: str) -> InMemoryVectorStore | None:
    return _store_cache.get(vector_store_id)


def set_vector_store(vector_store_id: str, store: InMemoryVectorStore) -> None:
    _store_cache[vector_store_id] = store


def make_search_code_tool(vector_store_id: str):
    @tool
    async def search_code(query: str, top_k: int = 10) -> list[dict]:
        """Search repository source files for code patterns, imports, or package usage."""
        store = _store_cache.get(vector_store_id)
        if store is None:
            return [{"error": f"Vector store {vector_store_id} not loaded"}]
        results = await store.asimilarity_search(query, k=top_k)
        return [
            {"file": doc.metadata.get("file", ""), "snippet": doc.page_content[:500]}
            for doc in results
        ]

    return search_code
```

- [ ] **Step 4: Create `src/main_graph/tools/code_impact.py`**

```python
from __future__ import annotations
from langchain_core.tools import tool
from src.main_graph.tools.search_code import _store_cache


def make_code_impact_tool(vector_store_id: str):
    @tool
    async def code_impact(package_name: str) -> list[dict]:
        """Find source files that import or use a specific npm package."""
        store = _store_cache.get(vector_store_id)
        if store is None:
            return [{"error": f"Vector store {vector_store_id} not loaded"}]
        query = f'import {package_name} require {package_name}'
        results = await store.asimilarity_search(query, k=20)
        return [
            {"file": doc.metadata.get("file", ""), "snippet": doc.page_content[:300]}
            for doc in results
            if package_name in doc.page_content
        ]

    return code_impact
```

- [ ] **Step 5: Create `src/main_graph/subgraphs/discovery/nodes/index_repository.py`**

```python
from __future__ import annotations
import logging
import os
import uuid
from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.main_graph.tools.search_code import set_vector_store, _SOURCE_EXTENSIONS

logger = logging.getLogger(__name__)

_embeddings = OpenAIEmbeddings()
_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)

_MAX_FILES = 200
_MAX_FILE_BYTES = 50_000


def _walk_source_files(repo_path: str) -> list[tuple[str, str]]:
    """Return list of (relative_path, content) for source files."""
    results = []
    skip_dirs = {"node_modules", ".git", "dist", "build", ".next", "coverage"}
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            if os.path.splitext(fname)[1] not in _SOURCE_EXTENSIONS:
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, repo_path)
            try:
                size = os.path.getsize(full)
                if size > _MAX_FILE_BYTES:
                    continue
                with open(full, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                results.append((rel, content))
                if len(results) >= _MAX_FILES:
                    return results
            except OSError:
                continue
    return results


async def index_repository(state: dict) -> dict:
    repo_path = state.get("repo_path", "")
    if not repo_path or not os.path.isdir(repo_path):
        logger.warning("index_repository: repo_path missing or not a dir, skipping")
        return {"vector_store_id": ""}

    files = _walk_source_files(repo_path)
    logger.info("index_repository: indexing %d source files", len(files))

    docs: list[Document] = []
    for rel_path, content in files:
        chunks = _splitter.split_text(content)
        for chunk in chunks:
            docs.append(Document(page_content=chunk, metadata={"file": rel_path}))

    vector_store_id = str(uuid.uuid4())
    store = InMemoryVectorStore(embedding=_embeddings)
    if docs:
        await store.aadd_documents(docs)

    set_vector_store(vector_store_id, store)
    logger.info("index_repository: vector_store_id=%s docs=%d", vector_store_id, len(docs))
    return {"vector_store_id": vector_store_id}
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/unit/tools/test_search_code.py -v
```
Expected: all 3 tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/main_graph/tools/search_code.py src/main_graph/tools/code_impact.py \
        src/main_graph/subgraphs/discovery/nodes/index_repository.py \
        tests/unit/tools/test_search_code.py
git commit -m "feat: repo indexer node and search_code / code_impact tools"
```

---

## Task 3: Prep subgraph — wire indexer + persist PrepResult

**Files:**
- Modify: `src/main_graph/subgraphs/discovery/state.py`
- Modify: `src/main_graph/subgraphs/discovery/constants.py`
- Create: `src/main_graph/subgraphs/discovery/nodes/save_prep_result.py`
- Modify: `src/main_graph/subgraphs/discovery/nodes/__init__.py`
- Modify: `src/main_graph/subgraphs/discovery/graph.py`
- Test: `tests/unit/test_prep_subgraph_routing.py`

**Interfaces:**
- Consumes: `DiscoveryState` (extended with `vector_store_id`)
- Produces: `DiscoveryState` with `prep_result_id: str` — this key maps back to `MainState`

- [ ] **Step 1: Write routing tests**

```python
# tests/unit/test_prep_subgraph_routing.py
from src.main_graph.subgraphs.discovery.graph import _route_after_clone, _route_after_inspect
from src.main_graph.subgraphs.discovery.constants import (
    BUILD_PROJECT_CONTEXT, INSPECT_REPO, INSTALL_DEPS, INDEX_REPO,
)


def test_clone_error_skips_to_summary():
    assert _route_after_clone({"discovery_error": "clone failed"}) == BUILD_PROJECT_CONTEXT


def test_clone_success_goes_to_inspect():
    assert _route_after_clone({"discovery_error": None}) == INSPECT_REPO


def test_inspect_no_lock_goes_to_install():
    assert _route_after_inspect({"has_lock_file": False}) == INSTALL_DEPS


def test_inspect_lock_present_goes_to_index():
    assert _route_after_inspect({"has_lock_file": True}) == INDEX_REPO
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_prep_subgraph_routing.py -v
```
Expected: `ImportError: cannot import name 'INDEX_REPO'`

- [ ] **Step 3: Update `src/main_graph/subgraphs/discovery/state.py`**

```python
"""State schema for the discovery subgraph."""
from typing import NotRequired
from typing_extensions import TypedDict


class ProjectMetadata(TypedDict):
    name: str
    package_manager: str
    direct_dependencies_count: int
    transitive_dependencies_count: int


class DiscoveryState(TypedDict):
    # Inputs
    job_id: str
    repo_url: str
    concern: str
    autopilot: bool

    # Set by nodes
    repo_path: NotRequired[str]
    manifest_files: NotRequired[list[str]]
    detected_package_manager: NotRequired[str]
    package_manager_version: NotRequired[str]
    has_lock_file: NotRequired[bool]
    docker_image: NotRequired[str]

    # New: set by index_repository
    vector_store_id: NotRequired[str]

    # Outputs
    project_metadata: NotRequired[ProjectMetadata]
    project_context: NotRequired[str]
    discovery_error: NotRequired[str | None]

    # New output: ID written back to MainState
    prep_result_id: NotRequired[str]
```

- [ ] **Step 4: Update `src/main_graph/subgraphs/discovery/constants.py`**

```python
"""Node name constants for the discovery subgraph."""
CLONE_REPO = "clone_repo"
INSPECT_REPO = "inspect_repo"
INSTALL_DEPS = "install_deps"
INDEX_REPO = "index_repo"
BUILD_PROJECT_CONTEXT = "build_project_context"
SAVE_PREP_RESULT = "save_prep_result"
```

- [ ] **Step 5: Create `src/main_graph/subgraphs/discovery/nodes/save_prep_result.py`**

```python
from __future__ import annotations
import json
import logging
import os

from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.models.results import PrepResult
from src.services.dependencies import get_result_dao

logger = logging.getLogger(__name__)


def _build_dependency_graph(repo_path: str) -> dict:
    """Read package.json and return {direct: {pkg: ver}, transitive: {}}."""
    pkg_path = os.path.join(repo_path or "", "package.json")
    try:
        with open(pkg_path) as f:
            pkg = json.load(f)
        direct = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        return {"direct": direct, "transitive": {}}
    except Exception:
        return {"direct": {}, "transitive": {}}


async def save_prep_result(state: DiscoveryState) -> dict:
    if state.get("discovery_error"):
        logger.info("save_prep_result: skipping due to discovery_error")
        return {}

    dao = get_result_dao()
    result = PrepResult(
        job_id=state["job_id"],
        repo_path=state.get("repo_path", ""),
        project_metadata=dict(state.get("project_metadata") or {}),
        manifest_files=state.get("manifest_files") or [],
        detected_package_manager=state.get("detected_package_manager") or "unknown",
        dependency_graph=_build_dependency_graph(state.get("repo_path", "")),
        sbom_cyclonedx={},
        discovery_summary=state.get("project_context") or "",
        vector_store_id=state.get("vector_store_id") or "",
    )
    prep_result_id = await dao.save_prep(result)
    logger.info("save_prep_result: saved prep_result_id=%s", prep_result_id)
    return {"prep_result_id": prep_result_id}
```

- [ ] **Step 6: Update `src/main_graph/subgraphs/discovery/nodes/__init__.py`**

```python
from .build_dependency_summary import build_project_context
from .clone_repo import clone_repo
from .index_repository import index_repository
from .inspect_repo import inspect_repo
from .install_deps import install_deps
from .save_prep_result import save_prep_result

__all__ = [
    "clone_repo", "inspect_repo", "install_deps",
    "build_project_context", "index_repository", "save_prep_result",
]
```

- [ ] **Step 7: Update `src/main_graph/subgraphs/discovery/graph.py`**

```python
from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.discovery.constants import (
    BUILD_PROJECT_CONTEXT, CLONE_REPO, INDEX_REPO,
    INSPECT_REPO, INSTALL_DEPS, SAVE_PREP_RESULT,
)
from src.main_graph.subgraphs.discovery.nodes import (
    build_project_context, clone_repo, index_repository,
    inspect_repo, install_deps, save_prep_result,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState


def _route_after_clone(state: DiscoveryState) -> str:
    return BUILD_PROJECT_CONTEXT if state.get("discovery_error") else INSPECT_REPO


def _route_after_inspect(state: DiscoveryState) -> str:
    return INSTALL_DEPS if not state.get("has_lock_file") else INDEX_REPO


def build_discovery_subgraph() -> StateGraph:
    builder = StateGraph(DiscoveryState)

    builder.add_node(CLONE_REPO, clone_repo)
    builder.add_node(INSPECT_REPO, inspect_repo)
    builder.add_node(INSTALL_DEPS, install_deps)
    builder.add_node(INDEX_REPO, index_repository)
    builder.add_node(BUILD_PROJECT_CONTEXT, build_project_context)
    builder.add_node(SAVE_PREP_RESULT, save_prep_result)

    builder.add_edge(START, CLONE_REPO)
    builder.add_conditional_edges(CLONE_REPO, _route_after_clone)
    builder.add_conditional_edges(INSPECT_REPO, _route_after_inspect)
    builder.add_edge(INSTALL_DEPS, INDEX_REPO)
    builder.add_edge(INDEX_REPO, BUILD_PROJECT_CONTEXT)
    builder.add_edge(BUILD_PROJECT_CONTEXT, SAVE_PREP_RESULT)
    builder.add_edge(SAVE_PREP_RESULT, END)

    return builder.compile()


discovery_subgraph = build_discovery_subgraph()
```

- [ ] **Step 8: Run tests**

```bash
uv run pytest tests/unit/test_prep_subgraph_routing.py -v
```
Expected: all 4 tests PASS

- [ ] **Step 9: Commit**

```bash
git add src/main_graph/subgraphs/discovery/state.py \
        src/main_graph/subgraphs/discovery/constants.py \
        src/main_graph/subgraphs/discovery/nodes/__init__.py \
        src/main_graph/subgraphs/discovery/nodes/save_prep_result.py \
        src/main_graph/subgraphs/discovery/graph.py \
        tests/unit/test_prep_subgraph_routing.py
git commit -m "feat: wire index_repository and save_prep_result into discovery subgraph"
```

---

## Task 4: Analysis — base agent runner and domain registry

**Files:**
- Create: `src/main_graph/subgraphs/analysis/__init__.py`
- Create: `src/main_graph/subgraphs/analysis/agents/base_agent.py`
- Create: `src/main_graph/subgraphs/analysis/agents/registry.py`
- Test: `tests/unit/test_base_agent.py`

**Interfaces:**
- Consumes: `AgentDispatch`, `PrepResult`, `DomainAgentDecision`, `FindingNote` from results/conductor models
- Produces: `run_react_loop(dispatch: AgentDispatch, prep: PrepResult, tools: list) -> EvidenceBundle`
- Produces: `AGENT_REGISTRY: dict[str, list[str]]` — `{"vulnerability_agent": ["npm_audit", "osv_lookup", "github_advisory"], ...}`
- Produces: `get_agent_tools(agent_type: str, prep: PrepResult) -> list` — returns configured LangChain tools

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_base_agent.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.models.results import AgentDispatch, EvidenceBundle, PrepResult, DomainAgentDecision
from src.models.conductor import FindingNote


def _prep() -> PrepResult:
    return PrepResult(
        job_id="j1", repo_path="/tmp/r", project_metadata={},
        manifest_files=[], detected_package_manager="npm",
        dependency_graph={}, sbom_cyclonedx={},
        discovery_summary="s", vector_store_id="vs1",
    )


def _dispatch(agent_type: str = "vulnerability_agent") -> AgentDispatch:
    return AgentDispatch(
        domain="vulnerabilities", hypothesis="check CVEs",
        packages_to_focus=["express"], agent_type=agent_type,
    )


@pytest.mark.asyncio
async def test_run_react_loop_returns_bundle_on_finalize():
    from src.main_graph.subgraphs.analysis.agents.base_agent import run_react_loop

    finding = FindingNote(dep_name="express", severity="high", description="CVE", evidence=[])
    final_decision = DomainAgentDecision(
        tool_calls=[], findings=[finding],
        summary="Found 1 CVE", confidence=0.9, finalize=True, reasoning="done",
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=final_decision)

    with patch("src.main_graph.subgraphs.analysis.agents.base_agent._llm", mock_llm):
        bundle = await run_react_loop(_dispatch(), _prep(), [])

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.domain == "vulnerabilities"
    assert bundle.confidence == 0.9
    assert len(bundle.findings) == 1


def test_agent_registry_has_expected_domains():
    from src.main_graph.subgraphs.analysis.agents.registry import AGENT_REGISTRY
    assert "vulnerability_agent" in AGENT_REGISTRY
    assert "maintenance_agent" in AGENT_REGISTRY
    assert "supply_chain_agent" in AGENT_REGISTRY
    assert "web_research_agent" in AGENT_REGISTRY


def test_get_agent_tools_returns_list():
    from src.main_graph.subgraphs.analysis.agents.registry import get_agent_tools
    tools = get_agent_tools("vulnerability_agent", _prep())
    assert isinstance(tools, list)
    assert len(tools) > 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_base_agent.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `src/main_graph/subgraphs/analysis/__init__.py`** (empty for now)

```python
```

- [ ] **Step 4: Create `src/main_graph/subgraphs/analysis/agents/base_agent.py`**

```python
from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid

from src.models.conductor import FindingNote, ToolCall, ToolResult
from src.models.results import AgentDispatch, DomainAgentDecision, EvidenceBundle, PrepResult
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 6
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = """\
You are a domain specialist investigating dependency risks for a Node.js project.
Your domain: {domain}
Hypothesis to investigate: {hypothesis}
Packages to focus on: {packages}

Each iteration output a DomainAgentDecision:
- tool_calls: tools to run in parallel (empty when done)
- findings: FindingNote list for risks discovered so far
- summary: concise summary of what you found
- confidence: float 0-1 reflecting evidence strength
- finalize: true when you have enough evidence
- reasoning: brief explanation of your next step

Available tools:
{tool_descriptions}

Rules:
- Never repeat a tool call with the same arguments.
- Populate evidence in each FindingNote with tool/url/log_snippet.
- Set finalize=true when confidence > 0.7 or you have exhausted relevant tools.
- After {max_iter} iterations, set finalize=true regardless.
"""


def _format_tools(tools: list) -> str:
    lines = []
    for t in tools:
        desc = getattr(t, "description", "") or ""
        lines.append(f"- {t.name}: {desc}")
    return "\n".join(lines) or "No tools available."


def _format_results(results: list[ToolResult]) -> str:
    if not results:
        return "No results yet."
    parts = []
    for tr in results[-10:]:
        val = f"ERROR: {tr.error}" if tr.error else json.dumps(tr.output, indent=2)[:1500]
        parts.append(f"[{tr.tool}] → {val}")
    return "\n\n".join(parts)


async def _run_tool(tc: ToolCall, tool_map: dict, prep: PrepResult) -> ToolResult:
    start = time.monotonic()
    fn = tool_map.get(tc.tool)
    if fn is None:
        return ToolResult(id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
                          output={}, error=f"unknown tool: {tc.tool}", duration_ms=0)
    try:
        # inject repo_path if the tool expects it
        import inspect
        sig = inspect.signature(fn.func if hasattr(fn, "func") else fn)
        kwargs = dict(tc.args)
        if "repo_path" in sig.parameters:
            kwargs["repo_path"] = prep.repo_path
        output = await fn.ainvoke(kwargs) if hasattr(fn, "ainvoke") else await fn(**kwargs)
        return ToolResult(id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
                          output=output if isinstance(output, dict) else {"result": output},
                          error=None, duration_ms=int((time.monotonic() - start) * 1000))
    except Exception as exc:
        logger.warning("base_agent tool %s failed: %s", tc.tool, exc)
        return ToolResult(id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
                          output={}, error=str(exc),
                          duration_ms=int((time.monotonic() - start) * 1000))


async def run_react_loop(
    dispatch: AgentDispatch,
    prep: PrepResult,
    tools: list,
) -> EvidenceBundle:
    tool_map = {t.name: t for t in tools}
    tool_results: list[ToolResult] = []
    decision: DomainAgentDecision | None = None

    structured = _llm.with_structured_output(DomainAgentDecision, method="function_calling")

    for iteration in range(_MAX_ITERATIONS):
        prompt = (
            f"Concern context: {prep.discovery_summary}\n\n"
            f"Tool results so far:\n{_format_results(tool_results)}\n\n"
            f"Iteration: {iteration + 1}/{_MAX_ITERATIONS}"
        )
        system = _SYSTEM.format(
            domain=dispatch.domain,
            hypothesis=dispatch.hypothesis,
            packages=", ".join(dispatch.packages_to_focus) or "all dependencies",
            tool_descriptions=_format_tools(tools),
            max_iter=_MAX_ITERATIONS,
        )
        decision = await structured.ainvoke([
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ])

        if decision.finalize or iteration == _MAX_ITERATIONS - 1:
            break

        if decision.tool_calls:
            new_results = await asyncio.gather(
                *[_run_tool(tc, tool_map, prep) for tc in decision.tool_calls]
            )
            tool_results.extend(new_results)

    return EvidenceBundle(
        domain=dispatch.domain,
        hypothesis=dispatch.hypothesis,
        findings=decision.findings if decision else [],
        summary=decision.summary if decision else "No results.",
        confidence=decision.confidence if decision else 0.0,
    )
```

- [ ] **Step 5: Create `src/main_graph/subgraphs/analysis/agents/registry.py`**

```python
from __future__ import annotations
from src.models.results import PrepResult
from src.main_graph.tools.search_code import make_search_code_tool

# domain agent_type → list of tool module paths to import and use
# Each entry is (module_path, function_name)
_TOOL_IMPORTS: dict[str, list[tuple[str, str]]] = {
    "vulnerability_agent": [
        ("src.main_graph.tools.npm_cli", "npm_audit"),
        ("src.main_graph.tools.external_api", "osv_lookup"),
        ("src.main_graph.tools.external_api", "github_advisory"),
    ],
    "maintenance_agent": [
        ("src.main_graph.tools.external_api", "unmaintained_packages"),
        ("src.main_graph.tools.external_api", "high_risk_packages"),
        ("src.main_graph.tools.external_api", "package_reputation"),
    ],
    "supply_chain_agent": [
        ("src.main_graph.tools.external_api", "typosquat_detection"),
        ("src.main_graph.tools.npm_cli", "resolve_transitive_parent"),
        ("src.main_graph.tools.package_files", "package_json"),
    ],
    "web_research_agent": [
        ("src.main_graph.tools.external_api", "web_search"),
        ("src.main_graph.tools.external_api", "github_advisory"),
        ("src.main_graph.tools.external_api", "osv_lookup"),
    ],
}

# Public registry: agent_type → tool name list (for description/logging)
AGENT_REGISTRY: dict[str, list[str]] = {
    k: [name for _, name in v] for k, v in _TOOL_IMPORTS.items()
}


def _import_tool(module_path: str, fn_name: str):
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, fn_name)


def get_agent_tools(agent_type: str, prep: PrepResult) -> list:
    """Return configured LangChain tools for the given agent_type."""
    import src.main_graph.tools.npm_cli  # noqa: F401
    import src.main_graph.tools.external_api  # noqa: F401
    import src.main_graph.tools.package_files  # noqa: F401

    imports = _TOOL_IMPORTS.get(agent_type, _TOOL_IMPORTS["web_research_agent"])
    tools = [_import_tool(mod, fn) for mod, fn in imports]

    if prep.vector_store_id:
        tools.append(make_search_code_tool(prep.vector_store_id))

    return tools
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/unit/test_base_agent.py -v
```
Expected: all 3 tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/main_graph/subgraphs/analysis/__init__.py \
        src/main_graph/subgraphs/analysis/agents/base_agent.py \
        src/main_graph/subgraphs/analysis/agents/registry.py \
        tests/unit/test_base_agent.py
git commit -m "feat: analysis base agent ReAct loop and domain registry"
```

---

## Task 5: Analysis subgraph — conductor, dispatcher, domain_agent, graph

**Files:**
- Create: `src/main_graph/subgraphs/analysis/state.py`
- Create: `src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py`
- Create: `src/main_graph/subgraphs/analysis/nodes/agent_dispatcher.py`
- Create: `src/main_graph/subgraphs/analysis/nodes/domain_agent.py`
- Create: `src/main_graph/subgraphs/analysis/nodes/evidence_collector.py`
- Create: `src/main_graph/subgraphs/analysis/nodes/save_analysis_result.py`
- Create: `src/main_graph/subgraphs/analysis/graph.py`
- Update: `src/main_graph/subgraphs/analysis/__init__.py`
- Test: `tests/unit/test_analysis_routing.py`

**Interfaces:**
- Consumes: `prep_result_id: str`, `concern: str`, `job_id: str` from MainState (key-name match)
- Produces: `analysis_result_id: str` written back to MainState

- [ ] **Step 1: Write routing tests**

```python
# tests/unit/test_analysis_routing.py
from src.main_graph.subgraphs.analysis.graph import _after_conductor
from src.models.results import AnalysisConductorDecision, AgentDispatch


def _decision(**kwargs) -> AnalysisConductorDecision:
    defaults = dict(dispatches=[], finalize=False, reasoning="r")
    return AnalysisConductorDecision(**{**defaults, **kwargs})


def test_finalize_goes_to_save():
    state = {"conductor_decision": _decision(finalize=True)}
    assert _after_conductor(state) == "save_analysis_result"


def test_dispatches_go_to_dispatcher():
    d = AgentDispatch(domain="vulnerabilities", hypothesis="h",
                      packages_to_focus=[], agent_type="vulnerability_agent")
    state = {"conductor_decision": _decision(dispatches=[d])}
    assert _after_conductor(state) == "agent_dispatcher"


def test_empty_decision_finalizes():
    assert _after_conductor({}) == "save_analysis_result"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_analysis_routing.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `src/main_graph/subgraphs/analysis/state.py`**

```python
from __future__ import annotations
import operator
from typing import Annotated, NotRequired
from typing_extensions import TypedDict
from src.models.results import AnalysisConductorDecision


class AnalysisState(TypedDict):
    # From MainState (matched by key name)
    job_id: str
    concern: str
    prep_result_id: str

    # Internal
    conductor_decision: NotRequired[AnalysisConductorDecision]
    current_dispatch: NotRequired[dict]   # AgentDispatch.model_dump() for domain_agent nodes
    bundle_ids: Annotated[list[str], operator.add]
    conductor_iteration: NotRequired[int]

    # Output (written back to MainState)
    analysis_result_id: NotRequired[str]
```

- [ ] **Step 4: Create `src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py`**

```python
from __future__ import annotations
import json
import logging

from src.models.results import AnalysisConductorDecision, PrepResult
from src.services.dependencies import get_result_dao
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 4
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = """\
You are a dependency risk investigation conductor. Given a user concern and project context,
you dispatch domain specialist agents to gather evidence.

Output an AnalysisConductorDecision:
- dispatches: list of AgentDispatch (agents to launch in parallel)
- finalize: true when you have dispatched enough agents and collected sufficient evidence
- reasoning: explain your strategy

Available agent types: vulnerability_agent, maintenance_agent, supply_chain_agent, web_research_agent

Rules:
- First iteration: always dispatch at least 2 agents relevant to the concern.
- Subsequent iterations: review bundle summaries; dispatch follow-up agents only if gaps remain.
- Set finalize=true when confidence across all bundles is sufficient (usually after 1-2 rounds).
- Limit packages_to_focus to the most relevant packages (max 10) per dispatch.
- Use web_research_agent for concerns not covered by the static agents.
- After {max_iter} iterations, set finalize=true.
"""


def _format_bundles(bundle_ids: list[str], bundles_data: list) -> str:
    if not bundles_data:
        return "No evidence collected yet."
    parts = []
    for b in bundles_data:
        parts.append(
            f"[{b.domain}] confidence={b.confidence:.2f}\n"
            f"  hypothesis: {b.hypothesis}\n"
            f"  summary: {b.summary}\n"
            f"  findings: {len(b.findings)}"
        )
    return "\n\n".join(parts)


async def analysis_conductor(state: AnalysisState) -> dict:
    iteration = (state.get("conductor_iteration") or 0) + 1
    dao = get_result_dao()

    prep: PrepResult = await dao.get_prep(state["prep_result_id"])

    bundle_ids = state.get("bundle_ids") or []
    bundles = await dao.get_bundles(bundle_ids) if bundle_ids else []

    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Project context:\n{prep.discovery_summary}\n\n"
        f"Package manager: {prep.detected_package_manager}\n"
        f"Direct dependencies: {list(prep.dependency_graph.get('direct', {}).keys())[:20]}\n\n"
        f"Evidence collected so far:\n{_format_bundles(bundle_ids, bundles)}\n\n"
        f"Iteration: {iteration}/{_MAX_ITERATIONS}"
    )

    system = _SYSTEM.format(max_iter=_MAX_ITERATIONS)
    structured = _llm.with_structured_output(AnalysisConductorDecision, method="function_calling")
    decision: AnalysisConductorDecision = await structured.ainvoke([
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ])

    if iteration >= _MAX_ITERATIONS:
        decision = decision.model_copy(update={"finalize": True})

    logger.info(
        "analysis_conductor: iteration=%d dispatches=%d finalize=%s",
        iteration, len(decision.dispatches), decision.finalize,
    )
    return {"conductor_decision": decision, "conductor_iteration": iteration}
```

- [ ] **Step 5: Create `src/main_graph/subgraphs/analysis/nodes/agent_dispatcher.py`**

```python
from __future__ import annotations
from langgraph.types import Send
from src.main_graph.subgraphs.analysis.agents.registry import AGENT_REGISTRY
from src.main_graph.subgraphs.analysis.state import AnalysisState


def agent_dispatcher(state: AnalysisState) -> list[Send]:
    decision = state.get("conductor_decision")
    if not decision or not decision.dispatches:
        return []

    sends = []
    for dispatch in decision.dispatches:
        agent_type = dispatch.agent_type if dispatch.agent_type in AGENT_REGISTRY else "web_research_agent"
        sends.append(Send("domain_agent", {
            **state,
            "current_dispatch": dispatch.model_dump(),
            "bundle_ids": [],  # reset accumulator for this branch
        }))
    return sends
```

- [ ] **Step 6: Create `src/main_graph/subgraphs/analysis/nodes/domain_agent.py`**

```python
from __future__ import annotations
import logging

from src.main_graph.subgraphs.analysis.agents.base_agent import run_react_loop
from src.main_graph.subgraphs.analysis.agents.registry import get_agent_tools
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.models.results import AgentDispatch
from src.services.dependencies import get_result_dao

logger = logging.getLogger(__name__)


async def domain_agent(state: AnalysisState) -> dict:
    dao = get_result_dao()
    prep = await dao.get_prep(state["prep_result_id"])
    dispatch = AgentDispatch(**state["current_dispatch"])
    tools = get_agent_tools(dispatch.agent_type, prep)

    logger.info("domain_agent: domain=%s hypothesis=%s", dispatch.domain, dispatch.hypothesis[:60])
    bundle = await run_react_loop(dispatch, prep, tools)
    bundle_id = await dao.save_bundle(bundle)

    logger.info("domain_agent: saved bundle_id=%s findings=%d", bundle_id, len(bundle.findings))
    return {"bundle_ids": [bundle_id]}
```

- [ ] **Step 7: Create `src/main_graph/subgraphs/analysis/nodes/evidence_collector.py`**

```python
from __future__ import annotations
from src.main_graph.subgraphs.analysis.state import AnalysisState


async def evidence_collector(state: AnalysisState) -> dict:
    """No-op fan-in node — triggers conductor re-entry after all domain agents finish."""
    return {}
```

- [ ] **Step 8: Create `src/main_graph/subgraphs/analysis/nodes/save_analysis_result.py`**

```python
from __future__ import annotations
import logging

from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.models.results import AnalysisResult
from src.services.dependencies import get_result_dao

logger = logging.getLogger(__name__)


async def save_analysis_result(state: AnalysisState) -> dict:
    dao = get_result_dao()
    bundle_ids = state.get("bundle_ids") or []
    bundles = await dao.get_bundles(bundle_ids)

    # Merge all findings from all bundles
    all_findings = [f for b in bundles for f in b.findings]

    result = AnalysisResult(
        job_id=state["job_id"],
        concern=state["concern"],
        findings=all_findings,
        evidence_bundle_ids=bundle_ids,
        iteration_count=state.get("conductor_iteration") or 0,
    )
    analysis_result_id = await dao.save_analysis(result)
    logger.info("save_analysis_result: saved analysis_result_id=%s findings=%d",
                analysis_result_id, len(all_findings))
    return {"analysis_result_id": analysis_result_id}
```

- [ ] **Step 9: Create `src/main_graph/subgraphs/analysis/graph.py`**

```python
from __future__ import annotations
from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import analysis_conductor
from src.main_graph.subgraphs.analysis.nodes.agent_dispatcher import agent_dispatcher
from src.main_graph.subgraphs.analysis.nodes.domain_agent import domain_agent
from src.main_graph.subgraphs.analysis.nodes.evidence_collector import evidence_collector
from src.main_graph.subgraphs.analysis.nodes.save_analysis_result import save_analysis_result
from src.main_graph.subgraphs.analysis.state import AnalysisState


def _after_conductor(state: AnalysisState) -> str:
    decision = state.get("conductor_decision")
    if not decision or decision.finalize:
        return "save_analysis_result"
    if decision.dispatches:
        return "agent_dispatcher"
    return "save_analysis_result"


def build_analysis_subgraph():
    builder = StateGraph(AnalysisState)

    builder.add_node("analysis_conductor", analysis_conductor)
    builder.add_node("agent_dispatcher", agent_dispatcher)
    builder.add_node("domain_agent", domain_agent)
    builder.add_node("evidence_collector", evidence_collector)
    builder.add_node("save_analysis_result", save_analysis_result)

    builder.add_edge(START, "analysis_conductor")
    builder.add_conditional_edges("analysis_conductor", _after_conductor,
                                  ["agent_dispatcher", "save_analysis_result"])
    builder.add_conditional_edges("agent_dispatcher", lambda s: agent_dispatcher(s), ["domain_agent"])
    builder.add_edge("domain_agent", "evidence_collector")
    builder.add_edge("evidence_collector", "analysis_conductor")
    builder.add_edge("save_analysis_result", END)

    return builder.compile()


analysis_subgraph = build_analysis_subgraph()
```

- [ ] **Step 10: Update `src/main_graph/subgraphs/analysis/__init__.py`**

```python
from .graph import analysis_subgraph

__all__ = ["analysis_subgraph"]
```

- [ ] **Step 11: Run routing tests**

```bash
uv run pytest tests/unit/test_analysis_routing.py -v
```
Expected: all 3 tests PASS

- [ ] **Step 12: Commit**

```bash
git add src/main_graph/subgraphs/analysis/
git commit -m "feat: analysis subgraph — conductor, agent dispatcher, domain agent, graph"
```

---

## Task 6: Report subgraph — conductor, tools, graph

**Files:**
- Create: `src/main_graph/subgraphs/report/state.py`
- Create: `src/main_graph/subgraphs/report/nodes/report_conductor.py`
- Create: `src/main_graph/subgraphs/report/nodes/report_tool_runner.py`
- Create: `src/main_graph/subgraphs/report/nodes/save_report_result.py`
- Create: `src/main_graph/subgraphs/report/graph.py`
- Create: `src/main_graph/subgraphs/report/__init__.py`
- Test: `tests/unit/test_report_routing.py`

**Interfaces:**
- Consumes: `analysis_result_id: str`, `prep_result_id: str`, `concern: str`, `job_id: str` from MainState
- Produces: `report_result_id: str` written back to MainState

- [ ] **Step 1: Write routing tests**

```python
# tests/unit/test_report_routing.py
from src.main_graph.subgraphs.report.graph import _after_conductor
from src.models.results import ReportConductorDecision
from src.models.conductor import ToolCall


def _decision(**kwargs) -> ReportConductorDecision:
    defaults = dict(tool_calls=[], finalize=False, reasoning="r")
    return ReportConductorDecision(**{**defaults, **kwargs})


def test_finalize_goes_to_save():
    assert _after_conductor({"conductor_decision": _decision(finalize=True)}) == "save_report_result"


def test_tool_calls_go_to_runner():
    tc = ToolCall(tool="web_search", args={"query": "q"}, reason="r")
    assert _after_conductor({"conductor_decision": _decision(tool_calls=[tc])}) == "report_tool_runner"


def test_empty_decision_finalizes():
    assert _after_conductor({}) == "save_report_result"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_report_routing.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `src/main_graph/subgraphs/report/state.py`**

```python
from __future__ import annotations
import operator
from typing import Annotated, NotRequired
from typing_extensions import TypedDict
from src.models.conductor import ToolResult
from src.models.results import ReportConductorDecision, ReportResult


class ReportState(TypedDict):
    # From MainState
    job_id: str
    concern: str
    prep_result_id: str
    analysis_result_id: str

    # Internal
    conductor_decision: NotRequired[ReportConductorDecision]
    tool_results: Annotated[list[ToolResult], operator.add]
    conductor_iteration: NotRequired[int]

    # Output
    report_result_id: NotRequired[str]
```

- [ ] **Step 4: Create `src/main_graph/subgraphs/report/nodes/report_conductor.py`**

```python
from __future__ import annotations
import json
import logging

from src.models.conductor import FindingNote, ToolResult
from src.models.results import AnalysisResult, PrepResult, ReportConductorDecision
from src.services.dependencies import get_result_dao
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 6
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = """\
You are a technical report writer. You enrich dependency risk findings with:
1. web_search — find safer alternatives and migration guides for risky packages
2. code_impact — find which source files use each risky package
3. get_findings — retrieve findings filtered by severity

For each high/critical finding, call both web_search and code_impact before finalizing.
Output a ReportConductorDecision:
- tool_calls: tools to run in parallel
- finalize: true when all high/critical findings are enriched
- reasoning: what you are doing

After {max_iter} iterations, set finalize=true.

Available tools:
- web_search(query): search for alternatives, CVE details, migration guides
- code_impact(package_name): find source files importing the package
- get_findings(severity): retrieve findings (severity: critical|high|medium|low|all)
"""


def _format_results(results: list[ToolResult]) -> str:
    if not results:
        return "No tool results yet."
    parts = []
    for tr in results[-15:]:
        val = f"ERROR: {tr.error}" if tr.error else json.dumps(tr.output, indent=2)[:1500]
        parts.append(f"[{tr.tool}] → {val}")
    return "\n\n".join(parts)


def _format_findings(findings: list[FindingNote]) -> str:
    return "\n".join(
        f"- [{f.severity.upper()}] {f.dep_name}: {f.description}"
        for f in findings
    )


async def report_conductor(state) -> dict:
    iteration = (state.get("conductor_iteration") or 0) + 1
    dao = get_result_dao()

    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])

    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Findings to enrich:\n{_format_findings(analysis.findings)}\n\n"
        f"Tool results so far:\n{_format_results(state.get('tool_results') or [])}\n\n"
        f"Iteration: {iteration}/{_MAX_ITERATIONS}"
    )
    system = _SYSTEM.format(max_iter=_MAX_ITERATIONS)
    structured = _llm.with_structured_output(ReportConductorDecision, method="function_calling")
    decision: ReportConductorDecision = await structured.ainvoke([
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ])

    if iteration >= _MAX_ITERATIONS:
        decision = decision.model_copy(update={"finalize": True})

    logger.info("report_conductor: iteration=%d tools=%d finalize=%s",
                iteration, len(decision.tool_calls), decision.finalize)
    return {"conductor_decision": decision, "conductor_iteration": iteration}
```

- [ ] **Step 5: Create `src/main_graph/subgraphs/report/nodes/report_tool_runner.py`**

```python
from __future__ import annotations
import asyncio
import inspect
import logging
import time
import uuid

from src.models.conductor import ToolCall, ToolResult
from src.models.results import AnalysisResult, PrepResult
from src.services.dependencies import get_result_dao
from src.main_graph.tools.code_impact import make_code_impact_tool
from src.main_graph.tools.external_api import web_search

logger = logging.getLogger(__name__)


async def _get_findings_tool(severity: str, analysis: AnalysisResult) -> dict:
    findings = analysis.findings
    if severity != "all":
        findings = [f for f in findings if f.severity == severity]
    return {"findings": [f.model_dump() for f in findings]}


async def _run_one(tc: ToolCall, prep: PrepResult, analysis: AnalysisResult) -> ToolResult:
    start = time.monotonic()
    try:
        if tc.tool == "get_findings":
            output = await _get_findings_tool(tc.args.get("severity", "all"), analysis)
        elif tc.tool == "web_search":
            output = await web_search(**tc.args)
        elif tc.tool == "code_impact":
            impact_tool = make_code_impact_tool(prep.vector_store_id)
            output = await impact_tool.ainvoke(tc.args)
            if not isinstance(output, dict):
                output = {"results": output}
        else:
            output = {"error": f"unknown tool: {tc.tool}"}
        return ToolResult(id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
                          output=output, error=None,
                          duration_ms=int((time.monotonic() - start) * 1000))
    except Exception as exc:
        logger.warning("report_tool_runner: tool=%s error=%s", tc.tool, exc)
        return ToolResult(id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
                          output={}, error=str(exc),
                          duration_ms=int((time.monotonic() - start) * 1000))


async def report_tool_runner(state) -> dict:
    decision = state.get("conductor_decision")
    if not decision or not decision.tool_calls:
        return {"tool_results": []}

    dao = get_result_dao()
    prep: PrepResult = await dao.get_prep(state["prep_result_id"])
    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])

    results = await asyncio.gather(
        *[_run_one(tc, prep, analysis) for tc in decision.tool_calls]
    )
    return {"tool_results": list(results)}
```

- [ ] **Step 6: Create `src/main_graph/subgraphs/report/nodes/save_report_result.py`**

```python
from __future__ import annotations
import json
import logging

from src.models.results import AnalysisResult, PrepResult, ReportFinding, ReportResult
from src.models.conductor import ToolResult
from src.services.dependencies import get_result_dao
from src.utils.llm import Model, get_llm, parse_llm_json

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

_SYSTEM = """\
You are a technical report writer. Given dependency risk findings and enrichment data
(web search results + code impact), produce a JSON report.

Output ONLY valid JSON:
{
  "executive_summary": "<2-4 sentence summary>",
  "overall_risk_level": "<critical|high|medium|low|none>",
  "findings": [
    {
      "dep_name": "<package>",
      "severity": "<critical|high|medium|low|info>",
      "description": "<concise description>",
      "recommendation": "<actionable fix>",
      "alternatives": ["<alternative package>"],
      "affected_files": ["<file:line>"],
      "evidence": [{"tool": "<tool>", "url": "<url or null>", "log_snippet": "<excerpt>"}]
    }
  ],
  "recommendations": ["<top-level recommendation>"]
}
"""


async def save_report_result(state) -> dict:
    dao = get_result_dao()
    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])
    tool_results: list[ToolResult] = state.get("tool_results") or []

    enrichment = "\n\n".join(
        f"[{tr.tool}({json.dumps(tr.args)})] → {json.dumps(tr.output, indent=2)[:1500]}"
        for tr in tool_results if not tr.error
    )

    findings_json = json.dumps([f.model_dump() for f in analysis.findings], indent=2)
    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Findings:\n{findings_json}\n\n"
        f"Enrichment data:\n{enrichment or 'None'}"
    )

    response = await _llm.ainvoke([
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user_prompt},
    ])

    try:
        data = parse_llm_json(response.content or "")
        findings = [ReportFinding(**f) for f in data.get("findings", [])]
    except Exception:
        findings = []
        data = {}

    overall = max(
        (f.severity for f in analysis.findings),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
        default="none",
    )

    result = ReportResult(
        job_id=state["job_id"],
        concern=state["concern"],
        executive_summary=data.get("executive_summary", ""),
        overall_risk_level=overall,
        findings=findings,
        recommendations=data.get("recommendations", []),
    )
    report_result_id = await dao.save_report(result)
    logger.info("save_report_result: saved report_result_id=%s findings=%d",
                report_result_id, len(findings))
    return {"report_result_id": report_result_id}
```

- [ ] **Step 7: Create `src/main_graph/subgraphs/report/graph.py`**

```python
from __future__ import annotations
from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.report.nodes.report_conductor import report_conductor
from src.main_graph.subgraphs.report.nodes.report_tool_runner import report_tool_runner
from src.main_graph.subgraphs.report.nodes.save_report_result import save_report_result
from src.main_graph.subgraphs.report.state import ReportState


def _after_conductor(state: ReportState) -> str:
    decision = state.get("conductor_decision")
    if not decision or decision.finalize:
        return "save_report_result"
    if decision.tool_calls:
        return "report_tool_runner"
    return "save_report_result"


def build_report_subgraph():
    builder = StateGraph(ReportState)

    builder.add_node("report_conductor", report_conductor)
    builder.add_node("report_tool_runner", report_tool_runner)
    builder.add_node("save_report_result", save_report_result)

    builder.add_edge(START, "report_conductor")
    builder.add_conditional_edges("report_conductor", _after_conductor,
                                  ["report_tool_runner", "save_report_result"])
    builder.add_edge("report_tool_runner", "report_conductor")
    builder.add_edge("save_report_result", END)

    return builder.compile()


report_subgraph = build_report_subgraph()
```

- [ ] **Step 8: Create `src/main_graph/subgraphs/report/__init__.py`**

```python
from .graph import report_subgraph

__all__ = ["report_subgraph"]
```

- [ ] **Step 9: Run routing tests**

```bash
uv run pytest tests/unit/test_report_routing.py -v
```
Expected: all 3 tests PASS

- [ ] **Step 10: Commit**

```bash
git add src/main_graph/subgraphs/report/
git commit -m "feat: report subgraph — conductor, tool runner, report generation"
```

---

## Task 7: Slim MainState and wire 3-node main graph

**Files:**
- Modify: `src/main_graph/state.py`
- Modify: `src/main_graph/constants.py`
- Modify: `src/main_graph/graph.py`
- Modify: `src/services/job_runner.py`
- Modify: `tests/unit/test_graph_routing.py`
- Test: `tests/unit/test_graph_routing.py` (rewrite)

**Interfaces:**
- MainState keys `prep_result_id`, `analysis_result_id`, `report_result_id` must match the output keys of each subgraph state

- [ ] **Step 1: Rewrite `tests/unit/test_graph_routing.py`**

```python
from src.main_graph.graph import _after_prep, _after_analysis
from src.main_graph.constants import ANALYSIS, REPORT
from langgraph.graph import END


def test_prep_error_goes_to_end():
    assert _after_prep({"discovery_error": "fail"}) == END


def test_prep_success_goes_to_analysis():
    assert _after_prep({"discovery_error": None, "prep_result_id": "p1"}) == ANALYSIS


def test_prep_no_result_id_goes_to_end():
    assert _after_prep({"discovery_error": None}) == END


def test_analysis_success_goes_to_report():
    assert _after_analysis({"analysis_result_id": "a1"}) == REPORT


def test_analysis_failure_goes_to_end():
    assert _after_analysis({}) == END
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_graph_routing.py -v
```
Expected: `ImportError: cannot import name '_after_analysis'`

- [ ] **Step 3: Update `src/main_graph/state.py`**

```python
from __future__ import annotations
from typing import Annotated, NotRequired
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class MainState(TypedDict):
    # Inputs
    repo_url: str
    concern: str
    job_id: str
    autopilot: bool

    # Inter-layer result IDs
    prep_result_id: NotRequired[str]
    analysis_result_id: NotRequired[str]
    report_result_id: NotRequired[str]

    # Control
    messages: Annotated[list, add_messages]
    cancelled: NotRequired[bool]
    discovery_error: NotRequired[str | None]
```

- [ ] **Step 4: Update `src/main_graph/constants.py`**

```python
PREP = "prep"
ANALYSIS = "analysis"
REPORT = "report"
```

- [ ] **Step 5: Update `src/main_graph/graph.py`**

```python
from __future__ import annotations
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.main_graph.constants import ANALYSIS, PREP, REPORT
from src.main_graph.state import MainState
from src.main_graph.subgraphs.analysis import analysis_subgraph
from src.main_graph.subgraphs.discovery import discovery_subgraph
from src.main_graph.subgraphs.report import report_subgraph


def _after_prep(state: MainState) -> str:
    if state.get("discovery_error") or not state.get("prep_result_id"):
        return END
    return ANALYSIS


def _after_analysis(state: MainState) -> str:
    if not state.get("analysis_result_id"):
        return END
    return REPORT


def build_main_graph():
    builder = StateGraph(MainState)

    builder.add_node(PREP, discovery_subgraph)
    builder.add_node(ANALYSIS, analysis_subgraph)
    builder.add_node(REPORT, report_subgraph)

    builder.add_edge(START, PREP)
    builder.add_conditional_edges(PREP, _after_prep, [ANALYSIS, END])
    builder.add_conditional_edges(ANALYSIS, _after_analysis, [REPORT, END])
    builder.add_edge(REPORT, END)

    return builder.compile(checkpointer=InMemorySaver())


main_graph = build_main_graph()
```

- [ ] **Step 6: Update `src/services/job_runner.py`**

Replace the body to track the new 3-node structure. Remove references to old constants (`CONDUCTOR`, `TOOL_RUNNER`, `HITL_GATE`, `REPORT_BUILDER`):

```python
"""Background task: run a job through the 3-layer pipeline."""
from __future__ import annotations
import logging
import shutil

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph import main_graph
from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter
from src.main_graph.constants import ANALYSIS, PREP, REPORT
from src.main_graph.subgraphs.discovery.tools.docker import make_docker_tool
from src.main_graph.tools.external_api import clear_cache
from src.models.job import JobStatus
from src.services.dependencies import get_result_dao
from src.utils.cost import CostCallback

logger = logging.getLogger(__name__)


def _build_config(job_id: str, dao: JobRepositoryPort, cost_cb: CostCallback) -> dict:
    container = DockerContainerAdapter()
    return {
        "configurable": {
            "thread_id": job_id,
            "job_repo": dao,
            "container": container,
            "docker_tool": make_docker_tool(container),
        },
        "callbacks": [cost_cb],
    }


async def _stream_graph(graph, input_data, config, dao: JobRepositoryPort, job_id: str) -> None:
    async for chunk in graph.astream(input_data, config, stream_mode="updates"):
        for node_name, node_update in chunk.items():
            logger.info("job=%s node=%s completed", job_id, node_name)

            if node_name == PREP:
                status = "failed" if node_update.get("discovery_error") else "done"
                await dao.complete_artifact(job_id, PREP, status)
                if status == "done":
                    await dao.start_artifact(job_id, ANALYSIS)

            elif node_name == ANALYSIS:
                await dao.complete_artifact(job_id, ANALYSIS, "done")
                await dao.start_artifact(job_id, REPORT)

            elif node_name == REPORT:
                report_result_id = node_update.get("report_result_id")
                await dao.complete_artifact(job_id, REPORT, "done")
                if report_result_id:
                    result_dao = get_result_dao()
                    report = await result_dao.get_report(report_result_id)
                    await dao.update_artifact_data(job_id, REPORT, {"output": report.model_dump()})


async def _finalize(dao: JobRepositoryPort, job_id: str, config: dict) -> None:
    clear_cache()
    snapshot = await main_graph.aget_state(config)
    values = snapshot.values

    if prep_result_id := values.get("prep_result_id"):
        result_dao = get_result_dao()
        try:
            prep = await result_dao.get_prep(prep_result_id)
            if prep.repo_path:
                shutil.rmtree(prep.repo_path, ignore_errors=True)
        except Exception:
            pass

    if values.get("cancelled"):
        await dao.mark_cancelled(job_id)
    elif values.get("discovery_error") or not values.get("prep_result_id"):
        await dao.mark_failed(job_id, error=values.get("discovery_error", "prep failed"))
    else:
        report_result_id = values.get("report_result_id", "")
        await dao.save_result(job_id, {"report_result_id": report_result_id})


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
    clear_cache()

    try:
        await _stream_graph(
            main_graph,
            {"repo_url": repo_url, "concern": concern, "job_id": job_id,
             "autopilot": autopilot, "messages": []},
            config, dao, job_id,
        )
        await dao.save_cost(job_id, cost_cb.cost())
        await _finalize(dao, job_id, config)
        await dao.update_status(job_id, JobStatus.done)

    except Exception as exc:
        logger.exception("job=%s unhandled error", job_id)
        clear_cache()
        await dao.save_cost(job_id, cost_cb.cost())
        await dao.mark_failed(job_id, error=str(exc))
```

- [ ] **Step 7: Run routing tests**

```bash
uv run pytest tests/unit/test_graph_routing.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 8: Run full unit test suite**

```bash
uv run pytest tests/unit/ -v
```
Expected: all tests PASS (fix any import errors from removed `CONDUCTOR`/`TOOL_RUNNER` constants)

- [ ] **Step 9: Commit**

```bash
git add src/main_graph/state.py src/main_graph/constants.py src/main_graph/graph.py \
        src/services/job_runner.py tests/unit/test_graph_routing.py
git commit -m "feat: wire 3-layer main graph — Prep → Analysis → Report"
```

---

## Task 8: Delete replaced code and verify end-to-end

**Files:**
- Delete: `src/main_graph/nodes/conductor.py`
- Delete: `src/main_graph/nodes/tool_runner.py`
- Delete: `src/main_graph/nodes/hitl_gate.py`
- Delete: `src/main_graph/nodes/report_builder.py`
- Verify: `uv run pytest` full suite passes

- [ ] **Step 1: Delete old nodes**

```bash
git rm src/main_graph/nodes/conductor.py \
       src/main_graph/nodes/tool_runner.py \
       src/main_graph/nodes/hitl_gate.py \
       src/main_graph/nodes/report_builder.py
```

- [ ] **Step 2: Run full test suite**

```bash
uv run pytest tests/ -v
```
Expected: all tests PASS with no import errors

- [ ] **Step 3: Smoke-test graph import**

```bash
uv run python -c "from src.main_graph import main_graph; print('graph nodes:', list(main_graph.get_graph().nodes.keys()))"
```
Expected output:
```
graph nodes: ['__start__', 'prep', 'analysis', 'report', '__end__']
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: delete replaced conductor/tool_runner/hitl/report_builder nodes"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Three subgraphs chained in main graph | Task 7 |
| Lean MainState with result IDs only | Task 7 |
| PrepResult persisted to MongoDB | Task 3 |
| Repo indexed into vector store during Prep | Task 2 + 3 |
| `search_code` tool shared across Analysis agents | Task 2 + 4 |
| Analysis conductor (ReAct) forms hypotheses | Task 5 |
| `Send()` fan-out to domain agents in parallel | Task 5 |
| Static domain registry + web_research fallback | Task 4 |
| EvidenceBundles written to MongoDB per agent | Task 5 |
| AnalysisResult persisted to MongoDB | Task 5 |
| Report conductor (ReAct) with tool loop | Task 6 |
| `web_search` tool in Report | Task 6 |
| `code_impact` tool using vector store | Task 2 + 6 |
| ReportFinding has `alternatives` + `affected_files` | Task 1 + 6 |
| ReportResult persisted to MongoDB | Task 6 |
| Old flat nodes deleted | Task 8 |

**Type consistency check:**
- `AnalysisConductorDecision` defined in Task 1, used in Task 5 ✓
- `DomainAgentDecision` defined in Task 1, used in Task 4 ✓
- `ReportConductorDecision` defined in Task 1, used in Task 6 ✓
- `EvidenceBundle` id field used in `save_bundle` / `get_bundles` ✓
- `bundle_ids: Annotated[list[str], operator.add]` in AnalysisState — accumulated by `domain_agent` returning `{"bundle_ids": [bundle_id]}` ✓
- `prep_result_id` key in DiscoveryState output matches MainState key ✓
- `analysis_result_id` key in AnalysisState output matches MainState key ✓
- `report_result_id` key in ReportState output matches MainState key ✓

**Note on `agent_dispatcher` graph wiring (Task 5, Step 9):** The `agent_dispatcher` node returns a `list[Send]` directly. In LangGraph, a node returning a list of `Send` objects is used with `add_conditional_edges` where the edge function *is* the dispatcher. Replace the inline lambda with a direct reference:

```python
# In build_analysis_subgraph():
builder.add_conditional_edges("agent_dispatcher", agent_dispatcher, ["domain_agent"])
```

This makes `agent_dispatcher` the routing function (not a node). Rename it accordingly: remove it from `add_node` and use it only as the edge function. Adjust Task 5 Step 5 and Step 9 so `agent_dispatcher` is the conditional edge function called after `analysis_conductor` when dispatches exist — not a separate node.
