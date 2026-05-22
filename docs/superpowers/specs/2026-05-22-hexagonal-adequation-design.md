# Hexagonal Adequation — main_graph and Subgraphs

**Date:** 2026-05-22
**Scope:** `apps/backend/src/main_graph/` and all subgraphs

---

## Problem

The rest of the backend follows hexagonal + DDD correctly: `JobRepositoryPort` defines the persistence contract, `JobDAO` implements it, and the API layer depends only on the port. The `main_graph` and its subgraphs break this pattern in four concrete ways:

1. **No ports for subgraph result DAOs** — each ingestion subgraph (`vulnerabilities`, `license_compliance`, `registry`, `repo`, `runtime`, `impact`) and `discovery` has a concrete DAO class used directly by nodes with no abstract port interface.
2. **`execute_plan.py` references `SUBGRAPH_DAOS`** — a dict of concrete singleton DAO instances exported from `__init__.py`, used to hydrate upstream results.
3. **Graph nodes call `get_job_repo()` and infrastructure utilities inline** — `orchestrator.py` and `execute_plan.py` pull `JobRepositoryPort`, vector store, and Docker/Trivy utilities directly rather than receiving them via injection.
4. **Nodes mix orchestration with business logic** — analysis logic (`_build_records`, intent classification, SBOM parsing) lives inside node functions alongside LangGraph wiring, making them untestable without a full graph runtime.

---

## Approach: Ports + RunnableConfig injection + node/service split

Three changes applied together:

1. **Define ports** for every infrastructure concern used inside `main_graph`.
2. **Inject all ports via `RunnableConfig` configurable** — `job_runner.py` assembles the concrete implementations once; nodes receive them through LangGraph's standard config parameter.
3. **Split each node** that contains business logic into a thin `node.py` (orchestration only) and a `service.py` (pure functions, no LangGraph imports).

---

## Section 1 — New ports

Four files added to `src/domain/ports/`:

### `ingestion_result_port.py`
Generic port covering all six ingestion subgraph DAOs and `SbomDAO`. All share the same `save`/`get` contract:

```python
from abc import ABC, abstractmethod
from typing import Any

class IngestionResultPort(ABC):
    @abstractmethod
    async def save(self, entry: Any) -> str: ...

    @abstractmethod
    async def get(self, doc_id: str) -> dict | None: ...
```

Concrete classes that implement this port (declare as base class, no logic changes):
`VulnerabilitiesDAO`, `LicenseComplianceDAO`, `RegistryDAO`, `RepoDAO`, `RuntimeDAO`, `ImpactDAO`, `SbomDAO`.

### `vector_store_port.py`
Covers `orchestrator.py`'s current `get_or_create_store()` usage:

```python
from abc import ABC, abstractmethod

class VectorStorePort(ABC):
    @abstractmethod
    async def add_texts(self, texts: list[str]) -> None: ...
```

### `container_run_port.py`
Single unified port for all Docker container operations. Both `run_docker_command` (LangChain tool, used by `inspector_agent` and `lock_generator_agent`) and `run_trivy` (direct subprocess, used by `vulnerabilities/analyze.py` and `generate_sbom.py`) are identical underneath — `asyncio.create_subprocess_exec("docker", "run", ...)`:

```python
from abc import ABC, abstractmethod

class ContainerRunPort(ABC):
    @abstractmethod
    async def run(
        self, image: str, command: str, volume: str | None = None
    ) -> tuple[int, str, str]:
        """Run a container. Returns (returncode, stdout, stderr)."""
        ...
```

One concrete adapter (`DockerContainerAdapter`) replaces both subprocess implementations.

For the LangChain tool case, the discovery tools become **factories** wrapping the port so agents receive an injectable tool:

```python
def make_docker_tool(container: ContainerRunPort):
    @tool
    async def run_docker_command(image: str, volume: str, command: str) -> str:
        rc, stdout, stderr = await container.run(image, command, volume)
        return json.dumps({"returncode": rc, "stdout": stdout, "stderr": stderr[:3000]})
    return run_docker_command
```

`run_trivy()` in `src/utils/trivy.py` becomes a service-layer helper that accepts a `ContainerRunPort` parameter instead of calling subprocess directly.

---

## Section 2 — Configurable shape

New file `src/main_graph/config.py` defines the typed structure for `config["configurable"]`:

```python
from typing_extensions import TypedDict
from langchain_core.runnables import RunnableConfig

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.domain.ports.vector_store_port import VectorStorePort
from src.domain.ports.container_run_port import ContainerRunPort


class PipelineConfigurable(TypedDict):
    job_repo: JobRepositoryPort
    vector_store: VectorStorePort
    container: ContainerRunPort
    ingestion_daos: dict[str, IngestionResultPort]  # keyed by subgraph name
    sbom_dao: IngestionResultPort


def get_services(config: RunnableConfig) -> PipelineConfigurable:
    return config["configurable"]
```

`job_runner.py` is the single assembly point — it builds this dict once and sets it in the LangGraph config:

```python
config = {
    "configurable": {
        "thread_id": job_id,
        "job_repo": dao,
        "vector_store": get_or_create_store(job_id),
        "container": DockerContainerAdapter(),
        "ingestion_daos": {
            "vulnerabilities": vulnerabilities_dao,
            "license_compliance": license_compliance_dao,
            "registry": registry_dao,
            "repo": repo_dao,
            "runtime": runtime_dao,
            "impact": impact_dao,
        },
        "sbom_dao": sbom_dao,
    }
}
```

`SUBGRAPH_DAOS` in `ingestion_subgraphs/__init__.py` is deleted — it existed only to give `execute_plan.py` access to concrete DAO singletons.

---

## Section 3 — Node/service split convention

**Rule:** a node file contains only LangGraph wiring — reads state, calls a service function, returns the state update. Business logic and infrastructure calls move to a `service.py` co-located in the same package.

### Nodes requiring a split

| Node | What moves to `service.py` |
|---|---|
| `vulnerabilities/nodes/analyze.py` | `_build_records`, Trivy call, DAO save |
| `license_compliance/nodes/analyze.py` | LLM prompt, parsing, DAO save |
| `registry/nodes/analyze.py` | LLM/tool calls, DAO save |
| `repo/nodes/analyze.py` | curator calls, DAO save |
| `runtime/nodes/analyze.py` | tool calls, DAO save |
| `impact/nodes/analyze.py` | LLM/tool calls, DAO save |
| `discovery/nodes/clone_repository.py` | Docker call, path logic |
| `discovery/nodes/generate_sbom.py` | Trivy call, SBOM parsing, DAO save |
| `discovery/nodes/lock_generator_agent.py` | Docker tool construction via `make_docker_tool` |
| `discovery/nodes/build_dependency_summary.py` | LLM prompt, DAO save |
| `orchestrator.py` | `_present_plan`, `_classify_intent`, vector store write, DAO push/update |
| `execute_plan.py` | Upstream hydration, subgraph invocation, DAO start/complete |

### Nodes that stay as-is (pure orchestration)

`execution_planner.py`, `stage_advance.py`, `task_dispatcher.py`, `planner.py`.

### Split pattern

```python
# vulnerabilities/nodes/analyze.py  — orchestration only
async def analyze(state: VulnerabilitiesState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await analyze_service(
        state,
        svc["container"],
        svc["ingestion_daos"]["vulnerabilities"],
    )

# vulnerabilities/service.py  — pure business logic, no LangGraph imports
async def analyze_service(
    state: VulnerabilitiesState,
    container: ContainerRunPort,
    dao: IngestionResultPort,
) -> dict:
    scan_data, _ = await run_trivy_scan(container, state["repo_path"], ...)
    records = _build_records(scan_data)
    result_id = await dao.save(VulnerabilitiesEntry(...))
    return {"result_id": result_id}
```

---

## Section 4 — Testing and architectural enforcement

### Unit testing service functions

Each `service.py` function takes ports as parameters — testable with `AsyncMock`, no MongoDB, no Docker, no LangGraph:

```python
async def test_analyze_vulnerabilities():
    mock_container = AsyncMock(spec=ContainerRunPort)
    mock_dao = AsyncMock(spec=IngestionResultPort)
    mock_container.run.return_value = (0, json.dumps({...}), "")
    mock_dao.save.return_value = "abc123"
    result = await analyze_service(state, mock_container, mock_dao)
    assert result["result_id"] == "abc123"
```

Node functions are too thin to need separate unit tests. Integration tests wire real adapters.

### Architectural purity tests

File `tests/architecture/test_boundaries.py` — static import-graph checks using `ast`, run in milliseconds:

```python
def test_domain_ports_are_pure():
    """domain/ports must not import from langgraph, motor, or services."""

def test_service_files_have_no_langgraph_imports():
    """subgraph service.py files must not import from langgraph."""

def test_nodes_only_access_infrastructure_via_config():
    """node files must not import concrete DAOs or src.utils.trivy directly."""
```

### Out of scope

`job_runner.py`'s artifact tracking loop hardcodes node names and inspects raw `node_update` dicts. Fixing that requires nodes to emit structured events — a separate concern that does not block this adequation.

LLM instances (`_llm` module-level singletons in `planner.py`, `orchestrator.py`, etc.) are intentionally not ported. The LLM is the core execution engine of the pipeline, not peripheral infrastructure — abstracting it behind a port would add indirection without hexagonal benefit.

---

## File change summary

**New files:**
- `src/domain/ports/ingestion_result_port.py`
- `src/domain/ports/vector_store_port.py`
- `src/domain/ports/container_run_port.py`
- `src/main_graph/config.py`
- `src/main_graph/adapters/docker_container_adapter.py`  ← shared adapter, not scoped to one subgraph
- `src/main_graph/subgraphs/discovery/service.py` (×4 nodes)
- `src/main_graph/subgraphs/ingestion_subgraphs/{name}/service.py` (×6 subgraphs)
- `src/main_graph/nodes/orchestrator_service.py`
- `src/main_graph/nodes/execute_plan_service.py`
- `tests/architecture/test_boundaries.py`

**Modified files:**
- `src/domain/ports/__init__.py` — re-export new ports
- `src/main_graph/subgraphs/ingestion_subgraphs/{name}/dao.py` (×6) — add `IngestionResultPort` as base class
- `src/main_graph/subgraphs/discovery/dao.py` — add `IngestionResultPort` as base class
- `src/main_graph/subgraphs/ingestion_subgraphs/__init__.py` — remove `SUBGRAPH_DAOS`
- `src/main_graph/nodes/orchestrator.py` — add `config` param, delegate to service
- `src/main_graph/nodes/execute_plan.py` — add `config` param, delegate to service
- `src/main_graph/subgraphs/discovery/nodes/*.py` — add `config` param, delegate to service
- `src/main_graph/subgraphs/ingestion_subgraphs/{name}/nodes/analyze.py` (×6) — add `config` param, delegate to service
- `src/services/job_runner.py` — assemble `PipelineConfigurable` in config dict
- `src/utils/trivy.py` — accept `ContainerRunPort` parameter
- `src/main_graph/subgraphs/discovery/tools/docker.py` — become factory (`make_docker_tool`)

**Deleted:**
- `SUBGRAPH_DAOS` export from `ingestion_subgraphs/__init__.py`
