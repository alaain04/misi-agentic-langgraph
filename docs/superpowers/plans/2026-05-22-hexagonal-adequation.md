# Hexagonal Adequation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align `main_graph` and all subgraphs with the hexagonal + DDD architecture already in place for the rest of the backend.

**Architecture:** Three changes applied together: (1) define ports for all infrastructure concerns, (2) inject all ports via `RunnableConfig` configurable assembled once in `job_runner.py`, (3) split each node that contains business logic into a thin orchestration `node.py` and a pure `service.py`.

**Tech Stack:** Python, LangGraph (`RunnableConfig`), LangChain tools, FastAPI, Motor/MongoDB, `uv` for package management.

---

## File Map

**New:**
- `src/domain/ports/ingestion_result_port.py` — generic port for all subgraph result DAOs
- `src/domain/ports/vector_store_port.py` — port for vector store
- `src/domain/ports/container_run_port.py` — unified port for all Docker operations
- `src/main_graph/config.py` — `PipelineConfigurable` TypedDict + `get_services()` helper
- `src/main_graph/adapters/docker_container_adapter.py` — concrete `ContainerRunPort` impl
- `src/main_graph/adapters/langchain_vector_store_adapter.py` — concrete `VectorStorePort` impl
- `src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/service.py`
- `src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/service.py`
- `src/main_graph/subgraphs/ingestion_subgraphs/registry/service.py`
- `src/main_graph/subgraphs/ingestion_subgraphs/repo/service.py`
- `src/main_graph/subgraphs/ingestion_subgraphs/runtime/service.py`
- `src/main_graph/subgraphs/ingestion_subgraphs/impact/service.py`
- `src/main_graph/subgraphs/discovery/service.py`
- `src/main_graph/nodes/orchestrator_service.py`
- `src/main_graph/nodes/execute_plan_service.py`
- `tests/architecture/test_boundaries.py`
- `tests/unit/subgraphs/test_vulnerabilities_service.py`
- `tests/unit/subgraphs/test_license_compliance_service.py`

**Modified:**
- `src/domain/ports/__init__.py` — re-export new ports
- `src/main_graph/subgraphs/ingestion_subgraphs/{name}/dao.py` (×6) — add `IngestionResultPort` base
- `src/main_graph/subgraphs/discovery/dao.py` — add `IngestionResultPort` base
- `src/main_graph/subgraphs/ingestion_subgraphs/__init__.py` — remove `SUBGRAPH_DAOS`
- `src/main_graph/subgraphs/discovery/tools/docker.py` — become factory `make_docker_tool()`
- `src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/nodes/analyze.py` — thin node
- `src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/nodes/analyze.py` — thin node
- `src/main_graph/subgraphs/ingestion_subgraphs/registry/nodes/analyze.py` — thin node
- `src/main_graph/subgraphs/ingestion_subgraphs/repo/nodes/analyze.py` — thin node
- `src/main_graph/subgraphs/ingestion_subgraphs/runtime/nodes/analyze.py` — thin node
- `src/main_graph/subgraphs/ingestion_subgraphs/impact/nodes/analyze.py` — thin node
- `src/main_graph/subgraphs/discovery/nodes/clone_repository.py` — thin node
- `src/main_graph/subgraphs/discovery/nodes/generate_sbom.py` — thin node
- `src/main_graph/subgraphs/discovery/nodes/lock_generator_agent.py` — thin node
- `src/main_graph/nodes/orchestrator.py` — thin node
- `src/main_graph/nodes/execute_plan.py` — thin node
- `src/services/job_runner.py` — assemble `PipelineConfigurable`
- `src/utils/trivy.py` — accept `ContainerRunPort` param

**Nodes with no changes needed** (pure logic or LLM-only, no infrastructure ports):
`execution_planner.py`, `stage_advance.py`, `task_dispatcher.py`, `planner.py`, `inspector_agent.py`, `build_dependency_summary.py`.

---

## Task 1: Define the three ports

**Files:**
- Create: `src/domain/ports/ingestion_result_port.py`
- Create: `src/domain/ports/vector_store_port.py`
- Create: `src/domain/ports/container_run_port.py`
- Modify: `src/domain/ports/__init__.py`

- [ ] **Write the port files**

`src/domain/ports/ingestion_result_port.py`:
```python
from abc import ABC, abstractmethod
from typing import Any


class IngestionResultPort(ABC):
    @abstractmethod
    async def save(self, entry: Any) -> str: ...

    @abstractmethod
    async def get(self, doc_id: str) -> dict | None: ...
```

`src/domain/ports/vector_store_port.py`:
```python
from abc import ABC, abstractmethod


class VectorStorePort(ABC):
    @abstractmethod
    async def add_texts(self, texts: list[str]) -> None: ...
```

`src/domain/ports/container_run_port.py`:
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

- [ ] **Update `src/domain/ports/__init__.py`** to re-export the new ports:
```python
from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.domain.ports.job_repository_port import JobRepositoryPort
from src.domain.ports.vector_store_port import VectorStorePort

__all__ = [
    "ContainerRunPort",
    "IngestionResultPort",
    "JobRepositoryPort",
    "VectorStorePort",
]
```

- [ ] **Verify imports work**
```bash
cd apps/backend && uv run python -c "from src.domain.ports import ContainerRunPort, IngestionResultPort, VectorStorePort; print('ok')"
```
Expected: `ok`

- [ ] **Commit**
```bash
git add apps/backend/src/domain/ports/
git commit -m "feat: add IngestionResultPort, VectorStorePort, ContainerRunPort"
```

---

## Task 2: Concrete adapters

**Files:**
- Create: `src/main_graph/adapters/__init__.py`
- Create: `src/main_graph/adapters/docker_container_adapter.py`
- Create: `src/main_graph/adapters/langchain_vector_store_adapter.py`

- [ ] **Create `src/main_graph/adapters/__init__.py`** (empty)

- [ ] **Create `src/main_graph/adapters/docker_container_adapter.py`**:
```python
import asyncio
import os

from src.domain.ports.container_run_port import ContainerRunPort

_TIMEOUT = 300


class DockerContainerAdapter(ContainerRunPort):
    async def run(
        self, image: str, command: str, volume: str | None = None
    ) -> tuple[int, str, str]:
        cmd = ["docker", "run", "--rm"]
        if volume:
            cmd += ["-v", volume]
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
        cmd += [image, "sh", "-c", command]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=_TIMEOUT
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", f"timed out after {_TIMEOUT}s"

        return (
            proc.returncode,
            stdout_b.decode(errors="replace"),
            stderr_b.decode(errors="replace")[:3000],
        )
```

- [ ] **Create `src/main_graph/adapters/langchain_vector_store_adapter.py`**:
```python
from src.domain.ports.vector_store_port import VectorStorePort


class LangchainVectorStoreAdapter(VectorStorePort):
    def __init__(self, store) -> None:
        self._store = store

    async def add_texts(self, texts: list[str]) -> None:
        await self._store.aadd_texts(texts)
```

- [ ] **Verify**
```bash
cd apps/backend && uv run python -c "from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter; from src.main_graph.adapters.langchain_vector_store_adapter import LangchainVectorStoreAdapter; print('ok')"
```
Expected: `ok`

- [ ] **Commit**
```bash
git add apps/backend/src/main_graph/adapters/
git commit -m "feat: add DockerContainerAdapter and LangchainVectorStoreAdapter"
```

---

## Task 3: Update `trivy.py` and convert `docker.py` to factory

**Files:**
- Modify: `src/utils/trivy.py`
- Modify: `src/main_graph/subgraphs/discovery/tools/docker.py`

- [ ] **Update `src/utils/trivy.py`** to accept a `ContainerRunPort` parameter:
```python
"""Shared Trivy runner via ContainerRunPort."""

import logging

from src.domain.ports.container_run_port import ContainerRunPort

logger = logging.getLogger(__name__)

_TRIVY_IMAGE = "aquasec/trivy:latest"
_TRIVY_VOLUME_TEMPLATE = "{repo_path}:/repo"


async def run_trivy(
    container: ContainerRunPort, repo_path: str, *trivy_args: str
) -> tuple[dict, str]:
    """Run a Trivy scan via ContainerRunPort. Returns (parsed_json, stderr)."""
    import json

    volume = _TRIVY_VOLUME_TEMPLATE.format(repo_path=repo_path)
    command = "fs --quiet " + " ".join(trivy_args) + " /repo"
    returncode, stdout, stderr = await container.run(
        _TRIVY_IMAGE, command, volume
    )

    if returncode != 0:
        raise RuntimeError(f"Trivy exited {returncode}: {stderr.strip()[:500]}")

    raw = stdout.strip()
    if not raw:
        return {}, stderr

    return json.loads(raw), stderr
```

- [ ] **Update `src/main_graph/subgraphs/discovery/tools/docker.py`** to be a factory:
```python
"""Factory for a LangChain Docker tool backed by ContainerRunPort."""

import json

from langchain_core.tools import tool

from src.domain.ports.container_run_port import ContainerRunPort


def make_docker_tool(container: ContainerRunPort):
    """Return a LangChain @tool that runs Docker commands via container port."""

    @tool
    async def run_docker_command(image: str, volume: str, command: str) -> str:
        """Run a shell command in a Docker container with the workspace mounted.

        Args:
            image: Docker image, e.g. "node:25-alpine"
            volume: Docker volume spec, e.g. "/host/path:/container/path"
            command: Shell command to run inside the container

        Returns JSON with keys: returncode (int), stdout (str), stderr (str).
        """
        returncode, stdout, stderr = await container.run(image, command, volume)
        return json.dumps(
            {"returncode": returncode, "stdout": stdout, "stderr": stderr}
        )

    return run_docker_command
```

- [ ] **Verify**
```bash
cd apps/backend && uv run python -c "from src.utils.trivy import run_trivy; from src.main_graph.subgraphs.discovery.tools.docker import make_docker_tool; print('ok')"
```
Expected: `ok`

- [ ] **Commit**
```bash
git add apps/backend/src/utils/trivy.py apps/backend/src/main_graph/subgraphs/discovery/tools/docker.py
git commit -m "refactor: trivy accepts ContainerRunPort; docker.py becomes make_docker_tool factory"
```

---

## Task 4: DAOs implement `IngestionResultPort`

**Files:** Modify 7 DAO files — add `IngestionResultPort` as base class to the primary result DAO in each.

- [ ] **`src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/dao.py`** — change class declaration:
```python
from src.domain.ports.ingestion_result_port import IngestionResultPort
# ... existing imports ...

class VulnerabilitiesDAO(IngestionResultPort):
    # ... rest unchanged ...
```

- [ ] **`src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/dao.py`**:
```python
from src.domain.ports.ingestion_result_port import IngestionResultPort

class LicenseComplianceDAO(IngestionResultPort):
    # ... rest unchanged ...
```

- [ ] **`src/main_graph/subgraphs/ingestion_subgraphs/registry/dao.py`**:
```python
from src.domain.ports.ingestion_result_port import IngestionResultPort

class RegistryDAO(IngestionResultPort):
    # ... rest unchanged ...
```

- [ ] **`src/main_graph/subgraphs/ingestion_subgraphs/repo/dao.py`** — only `RepoDAO`, not `RepoCacheDAO`:
```python
from src.domain.ports.ingestion_result_port import IngestionResultPort

class RepoDAO(IngestionResultPort):
    # ... rest unchanged ...

class RepoCacheDAO:   # unchanged — cache DAO, different interface
    # ... rest unchanged ...
```

- [ ] **`src/main_graph/subgraphs/ingestion_subgraphs/runtime/dao.py`** — only `RuntimeDAO`:
```python
from src.domain.ports.ingestion_result_port import IngestionResultPort

class RuntimeDAO(IngestionResultPort):
    # ... rest unchanged ...

class RuntimeCacheDAO:   # unchanged
    # ... rest unchanged ...
```

- [ ] **`src/main_graph/subgraphs/ingestion_subgraphs/impact/dao.py`**:
```python
from src.domain.ports.ingestion_result_port import IngestionResultPort

class ImpactDAO(IngestionResultPort):
    # ... rest unchanged ...
```

- [ ] **`src/main_graph/subgraphs/discovery/dao.py`**:
```python
from src.domain.ports.ingestion_result_port import IngestionResultPort

class SbomDAO(IngestionResultPort):
    # ... rest unchanged ...
```

- [ ] **Verify all DAOs satisfy the port**
```bash
cd apps/backend && uv run python -c "
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.dao import VulnerabilitiesDAO
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.dao import LicenseComplianceDAO
from src.main_graph.subgraphs.ingestion_subgraphs.registry.dao import RegistryDAO
from src.main_graph.subgraphs.ingestion_subgraphs.repo.dao import RepoDAO
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.dao import RuntimeDAO
from src.main_graph.subgraphs.ingestion_subgraphs.impact.dao import ImpactDAO
from src.main_graph.subgraphs.discovery.dao import SbomDAO
from src.domain.ports.ingestion_result_port import IngestionResultPort
for cls in [VulnerabilitiesDAO, LicenseComplianceDAO, RegistryDAO, RepoDAO, RuntimeDAO, ImpactDAO, SbomDAO]:
    assert issubclass(cls, IngestionResultPort), cls
print('ok')
"
```
Expected: `ok`

- [ ] **Commit**
```bash
git add apps/backend/src/main_graph/subgraphs/
git commit -m "refactor: primary subgraph DAOs implement IngestionResultPort"
```

---

## Task 5: `PipelineConfigurable` and `get_services()`

**Files:**
- Create: `src/main_graph/config.py`

- [ ] **Create `src/main_graph/config.py`**:
```python
"""Typed configurable dict for all pipeline infrastructure ports."""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from typing_extensions import TypedDict

from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.domain.ports.job_repository_port import JobRepositoryPort
from src.domain.ports.vector_store_port import VectorStorePort
from src.main_graph.subgraphs.ingestion_subgraphs.repo.dao import RepoCacheDAO
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.dao import RuntimeCacheDAO


class PipelineConfigurable(TypedDict):
    job_repo: JobRepositoryPort
    vector_store: VectorStorePort
    container: ContainerRunPort
    docker_tool: BaseTool
    ingestion_daos: dict[str, IngestionResultPort]
    sbom_dao: IngestionResultPort
    repo_cache_dao: RepoCacheDAO
    runtime_cache_dao: RuntimeCacheDAO


def get_services(config: RunnableConfig) -> PipelineConfigurable:
    return config["configurable"]
```

- [ ] **Verify import**
```bash
cd apps/backend && uv run python -c "from src.main_graph.config import PipelineConfigurable, get_services; print('ok')"
```
Expected: `ok`

- [ ] **Commit**
```bash
git add apps/backend/src/main_graph/config.py
git commit -m "feat: add PipelineConfigurable and get_services helper"
```

---

## Task 6: Wire `job_runner.py` to assemble the configurable

**Files:**
- Modify: `src/services/job_runner.py`

- [ ] **Update `src/services/job_runner.py`** — replace the two `config = {"configurable": {"thread_id": job_id}}` lines (in `run_analysis` and `resume_analysis`) with the full assembly. Also add the necessary imports at the top:

Add to imports:
```python
from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter
from src.main_graph.adapters.langchain_vector_store_adapter import LangchainVectorStoreAdapter
from src.main_graph.subgraphs.discovery.tools.docker import make_docker_tool
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.dao import vulnerabilities_dao
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.dao import license_compliance_dao
from src.main_graph.subgraphs.ingestion_subgraphs.registry.dao import registry_dao
from src.main_graph.subgraphs.ingestion_subgraphs.repo.dao import repo_dao, repo_cache_dao
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.dao import runtime_dao, runtime_cache_dao
from src.main_graph.subgraphs.ingestion_subgraphs.impact.dao import impact_dao
from src.main_graph.subgraphs.discovery.dao import sbom_dao
```

Add a helper function before `run_analysis`:
```python
def _build_config(job_id: str, dao: JobRepositoryPort) -> dict:
    container = DockerContainerAdapter()
    store = get_or_create_store(job_id)
    return {
        "configurable": {
            "thread_id": job_id,
            "job_repo": dao,
            "vector_store": LangchainVectorStoreAdapter(store),
            "container": container,
            "docker_tool": make_docker_tool(container),
            "ingestion_daos": {
                "vulnerabilities": vulnerabilities_dao,
                "license_compliance": license_compliance_dao,
                "registry": registry_dao,
                "repo": repo_dao,
                "runtime": runtime_dao,
                "impact": impact_dao,
            },
            "sbom_dao": sbom_dao,
            "repo_cache_dao": repo_cache_dao,
            "runtime_cache_dao": runtime_cache_dao,
        }
    }
```

In `run_analysis`, replace:
```python
config = {"configurable": {"thread_id": job_id}}
```
with:
```python
config = _build_config(job_id, dao)
```

In `resume_analysis`, replace:
```python
config = {"configurable": {"thread_id": job_id}}
```
with:
```python
config = _build_config(job_id, dao)
```

- [ ] **Verify import**
```bash
cd apps/backend && uv run python -c "from src.services.job_runner import run_analysis; print('ok')"
```
Expected: `ok`

- [ ] **Commit**
```bash
git add apps/backend/src/services/job_runner.py
git commit -m "refactor: job_runner assembles PipelineConfigurable in config"
```

---

## Task 7: Vulnerabilities — service + thin node

**Files:**
- Create: `src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/service.py`
- Modify: `src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/nodes/analyze.py`
- Create: `tests/unit/subgraphs/test_vulnerabilities_service.py`

- [ ] **Write failing test** `tests/unit/subgraphs/test_vulnerabilities_service.py`:
```python
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.ingestion_result_port import IngestionResultPort


@pytest.mark.asyncio
async def test_analyze_service_saves_records():
    from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.service import (
        analyze_service,
    )

    trivy_output = {
        "Results": [
            {
                "Vulnerabilities": [
                    {
                        "PkgName": "lodash",
                        "InstalledVersion": "4.17.15",
                        "VulnerabilityID": "CVE-2021-23337",
                        "Severity": "HIGH",
                        "Description": "Prototype pollution",
                        "FixedVersion": "4.17.21",
                    }
                ]
            }
        ]
    }
    container = AsyncMock(spec=ContainerRunPort)
    container.run.return_value = (0, json.dumps(trivy_output), "")
    dao = AsyncMock(spec=IngestionResultPort)
    dao.save.return_value = "result_abc"

    state = {"repo_path": "/tmp/repo", "concern": "security"}
    result = await analyze_service(state, container, dao)

    assert result == {"result_id": "result_abc"}
    dao.save.assert_awaited_once()
    saved_entry = dao.save.call_args[0][0]
    assert saved_entry.total_findings == 1
    assert saved_entry.records[0].name == "lodash"


@pytest.mark.asyncio
async def test_analyze_service_no_repo_path():
    from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.service import (
        analyze_service,
    )

    container = AsyncMock(spec=ContainerRunPort)
    dao = AsyncMock(spec=IngestionResultPort)
    dao.save.return_value = "empty_id"

    result = await analyze_service({"repo_path": "", "concern": "sec"}, container, dao)

    assert result == {"result_id": "empty_id"}
    container.run.assert_not_awaited()
```

- [ ] **Run test — verify it fails**
```bash
cd apps/backend && uv run pytest tests/unit/subgraphs/test_vulnerabilities_service.py -v
```
Expected: `ImportError` or `ModuleNotFoundError` for `analyze_service`

- [ ] **Create `src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/service.py`**:
```python
"""Vulnerabilities analysis — pure business logic."""

import logging

from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.models import (
    VulnerabilitiesEntry,
    VulnerabilityFinding,
    VulnerabilityRecord,
)
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.state import (
    VulnerabilitiesState,
)
from src.utils.trivy import run_trivy

logger = logging.getLogger(__name__)


def _severity_rank(s: str) -> int:
    return {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s.upper(), 0)


def _build_records(raw_vulns: list[dict]) -> list[VulnerabilityRecord]:
    by_pkg: dict[str, list[VulnerabilityFinding]] = {}
    versions: dict[str, str] = {}
    for v in raw_vulns:
        pkg = v.get("PkgName", "")
        if not pkg:
            continue
        versions.setdefault(pkg, v.get("InstalledVersion", "unknown"))
        by_pkg.setdefault(pkg, []).append(
            VulnerabilityFinding(
                cve_id=v.get("VulnerabilityID") or None,
                severity=v.get("Severity", "UNKNOWN"),
                description=v.get("Description") or None,
                fixed_in=v.get("FixedVersion") or None,
            )
        )
    return [
        VulnerabilityRecord(
            name=pkg,
            version=versions[pkg],
            findings=findings,
            risk_level=max(findings, key=lambda f: _severity_rank(f.severity)).severity.lower(),
        )
        for pkg, findings in by_pkg.items()
    ]


async def analyze_service(
    state: VulnerabilitiesState,
    container: ContainerRunPort,
    dao: IngestionResultPort,
) -> dict:
    repo_path = state.get("repo_path", "")
    concern = state.get("concern", "")

    if not repo_path:
        logger.error("vulnerabilities: no repo_path in state")
        entry = VulnerabilitiesEntry(records=[], total_findings=0, concern=concern)
        result_id = await dao.save(entry)
        return {"result_id": result_id}

    scan_data: dict = {}
    try:
        logger.info("vulnerabilities: running Trivy vuln scan on %s", repo_path)
        scan_data, _ = await run_trivy(container, repo_path, "--format", "json", "--scanners", "vuln")
    except Exception as exc:
        logger.exception("vulnerabilities: Trivy scan failed: %s", exc)

    raw_vulns = [
        v
        for result in scan_data.get("Results", [])
        for v in (result.get("Vulnerabilities") or [])
    ]
    records = _build_records(raw_vulns)
    entry = VulnerabilitiesEntry(
        records=records,
        total_findings=sum(len(r.findings) for r in records),
        concern=concern,
    )
    result_id = await dao.save(entry)
    logger.info("vulnerabilities: %d packages, result_id=%s", len(records), result_id)
    return {"result_id": result_id}
```

- [ ] **Run test — verify it passes**
```bash
cd apps/backend && uv run pytest tests/unit/subgraphs/test_vulnerabilities_service.py -v
```
Expected: `2 passed`

- [ ] **Thin out `src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/nodes/analyze.py`**:
```python
"""Vulnerabilities analysis node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.service import (
    analyze_service,
)
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.state import (
    VulnerabilitiesState,
)


async def analyze(state: VulnerabilitiesState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await analyze_service(
        state,
        svc["container"],
        svc["ingestion_daos"]["vulnerabilities"],
    )
```

- [ ] **Run tests again**
```bash
cd apps/backend && uv run pytest tests/unit/subgraphs/test_vulnerabilities_service.py -v
```
Expected: `2 passed`

- [ ] **Commit**
```bash
git add apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/
git add apps/backend/tests/unit/subgraphs/test_vulnerabilities_service.py
git commit -m "refactor: vulnerabilities — service.py + thin node, inject ContainerRunPort"
```

---

## Task 8: License compliance — service + thin node

**Files:**
- Create: `src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/service.py`
- Modify: `src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/nodes/analyze.py`
- Create: `tests/unit/subgraphs/test_license_compliance_service.py`

- [ ] **Write failing test** `tests/unit/subgraphs/test_license_compliance_service.py`:
```python
import json
from unittest.mock import AsyncMock

import pytest

from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.ingestion_result_port import IngestionResultPort


@pytest.mark.asyncio
async def test_analyze_service_records_violations():
    from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.service import (
        analyze_service,
    )

    trivy_output = {
        "Results": [
            {
                "Licenses": [
                    {"PkgName": "react", "Name": "GPL-2.0", "Category": "restricted"},
                    {"PkgName": "lodash", "Name": "MIT", "Category": "permissive"},
                ]
            }
        ]
    }
    container = AsyncMock(spec=ContainerRunPort)
    container.run.return_value = (0, json.dumps(trivy_output), "")
    dao = AsyncMock(spec=IngestionResultPort)
    dao.save.return_value = "lic_id"

    result = await analyze_service({"repo_path": "/r", "concern": "licenses"}, container, dao)

    assert result == {"result_id": "lic_id"}
    entry = dao.save.call_args[0][0]
    assert entry.total_violations == 1
```

- [ ] **Run test — verify it fails**
```bash
cd apps/backend && uv run pytest tests/unit/subgraphs/test_license_compliance_service.py -v
```
Expected: `ImportError`

- [ ] **Create `src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/service.py`**:
```python
"""License compliance analysis — pure business logic."""

import logging

from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.models import (
    LicenseComplianceEntry,
    LicenseRecord,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.state import (
    LicenseComplianceState,
)
from src.utils.trivy import run_trivy

logger = logging.getLogger(__name__)

_RISKY_CATEGORIES = {"restricted", "reciprocal", "unknown"}
_RISKY_LICENSES = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.0", "LGPL-2.1"}


def _risk_level(category: str, license_name: str) -> str:
    cat = category.lower()
    if cat in _RISKY_CATEGORIES or license_name in _RISKY_LICENSES:
        return "high"
    if cat in {"notice", "permissive"}:
        return "low"
    return "medium"


def _is_compliant(category: str, license_name: str) -> bool:
    return category.lower() != "restricted" and license_name not in _RISKY_LICENSES


async def analyze_service(
    state: LicenseComplianceState,
    container: ContainerRunPort,
    dao: IngestionResultPort,
) -> dict:
    repo_path = state.get("repo_path", "")
    concern = state.get("concern", "")

    if not repo_path:
        logger.error("license_compliance: no repo_path in state")
        entry = LicenseComplianceEntry(records=[], total_violations=0, concern=concern)
        result_id = await dao.save(entry)
        return {"result_id": result_id}

    scan_data: dict = {}
    try:
        logger.info("license_compliance: running Trivy license scan on %s", repo_path)
        scan_data, _ = await run_trivy(container, repo_path, "--format", "json", "--scanners", "license")
    except Exception as exc:
        logger.exception("license_compliance: Trivy scan failed: %s", exc)

    raw_licenses = [
        lic
        for result in scan_data.get("Results", [])
        for lic in (result.get("Licenses") or [])
    ]
    records = [
        LicenseRecord(
            name=lic.get("PkgName", ""),
            version="",
            license=lic.get("Name") or None,
            is_compliant=_is_compliant(lic.get("Category", "unknown"), lic.get("Name", "")),
            risk_level=_risk_level(lic.get("Category", "unknown"), lic.get("Name", "")),
            notes=f"category={lic.get('Category', 'unknown')}",
        )
        for lic in raw_licenses
        if lic.get("PkgName")
    ]
    entry = LicenseComplianceEntry(
        records=records,
        total_violations=sum(1 for r in records if not r.is_compliant),
        concern=concern,
    )
    result_id = await dao.save(entry)
    logger.info("license_compliance: %d records, %d violations, result_id=%s", len(records), entry.total_violations, result_id)
    return {"result_id": result_id}
```

- [ ] **Run test — verify it passes**
```bash
cd apps/backend && uv run pytest tests/unit/subgraphs/test_license_compliance_service.py -v
```
Expected: `1 passed`

- [ ] **Thin out `src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/nodes/analyze.py`**:
```python
"""License compliance analysis node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.service import (
    analyze_service,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.state import (
    LicenseComplianceState,
)


async def analyze(state: LicenseComplianceState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await analyze_service(
        state,
        svc["container"],
        svc["ingestion_daos"]["license_compliance"],
    )
```

- [ ] **Run tests**
```bash
cd apps/backend && uv run pytest tests/unit/subgraphs/ -v
```
Expected: all pass

- [ ] **Commit**
```bash
git add apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/
git add apps/backend/tests/unit/subgraphs/test_license_compliance_service.py
git commit -m "refactor: license_compliance — service.py + thin node"
```

---

## Task 9: Registry — service + thin node

**Files:**
- Create: `src/main_graph/subgraphs/ingestion_subgraphs/registry/service.py`
- Modify: `src/main_graph/subgraphs/ingestion_subgraphs/registry/nodes/analyze.py`

Note: `registry/analyze.py` calls `get_db()["npm_package_cache"]` directly. This call moves to `service.py`. Service files may access `get_db()` directly — the purity constraint applies to NODE files only.

- [ ] **Create `src/main_graph/subgraphs/ingestion_subgraphs/registry/service.py`**:
```python
"""Registry analysis — pure business logic."""

from __future__ import annotations

import logging

from src.db.connection import get_db
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.main_graph.subgraphs.ingestion_subgraphs.registry.models import RegistryEntry
from src.main_graph.subgraphs.ingestion_subgraphs.registry.state import RegistryState
from src.utils.workers_client import ingest_and_wait

_log = logging.getLogger(__name__)


def _extract_entry(dep_name: str, doc: dict) -> RegistryEntry:
    time_data = doc.get("time") or {}
    last_publish = time_data.get("modified")
    downloads = doc.get("downloads") or {}
    weekly_downloads: int | None = None
    if isinstance(downloads, dict):
        weekly_downloads = downloads.get("weekly") or downloads.get("last-week")
    elif isinstance(downloads, int):
        weekly_downloads = downloads
    deprecated = doc.get("deprecated")
    is_deprecated = bool(deprecated)
    maintainers = doc.get("maintainers") or []
    maintainers_count = len(maintainers) if isinstance(maintainers, list) else None
    return RegistryEntry(
        dep_name=dep_name,
        last_publish=last_publish,
        weekly_downloads=weekly_downloads,
        is_deprecated=is_deprecated,
        maintainers_count=maintainers_count,
    )


async def analyze_service(state: RegistryState, dao: IngestionResultPort) -> dict:
    dep_name = state.get("dependency_name", "")
    if not dep_name:
        result_id = await dao.save(RegistryEntry())
        return {"result_id": result_id}

    npm_cache = get_db()["npm_package_cache"]
    cached_doc = await npm_cache.find_one({"name": dep_name})
    if cached_doc is None:
        try:
            await ingest_and_wait(["npm"], [dep_name])
        except Exception as exc:
            _log.warning("registry: workers ingest failed for %s: %s", dep_name, exc)
            result_id = await dao.save(RegistryEntry(dep_name=dep_name))
            return {"result_id": result_id}
        cached_doc = await npm_cache.find_one({"name": dep_name})

    if cached_doc is None:
        _log.warning("registry: no npm data found for %s after ingest", dep_name)
        result_id = await dao.save(RegistryEntry(dep_name=dep_name))
        return {"result_id": result_id}

    entry = _extract_entry(dep_name, cached_doc)
    result_id = await dao.save(entry)
    _log.info("registry: saved %s, result_id=%s", dep_name, result_id)
    return {"result_id": result_id}
```

- [ ] **Thin out `src/main_graph/subgraphs/ingestion_subgraphs/registry/nodes/analyze.py`**:
```python
"""Registry analysis node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.ingestion_subgraphs.registry.service import analyze_service
from src.main_graph.subgraphs.ingestion_subgraphs.registry.state import RegistryState


async def analyze(state: RegistryState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await analyze_service(state, svc["ingestion_daos"]["registry"])
```

- [ ] **Verify import**
```bash
cd apps/backend && uv run python -c "from src.main_graph.subgraphs.ingestion_subgraphs.registry.nodes.analyze import analyze; print('ok')"
```
Expected: `ok`

- [ ] **Commit**
```bash
git add apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/
git commit -m "refactor: registry — service.py + thin node"
```

---

## Task 10: Repo — service + thin node

**Files:**
- Create: `src/main_graph/subgraphs/ingestion_subgraphs/repo/service.py`
- Modify: `src/main_graph/subgraphs/ingestion_subgraphs/repo/nodes/analyze.py`

Note: `repo/analyze.py` calls `get_db()` for workers cache collections and uses `repo_cache_dao`. Both move to `service.py`. `repo_cache_dao` is injected via the `repo_cache_dao` configurable key.

- [ ] **Create `src/main_graph/subgraphs/ingestion_subgraphs/repo/service.py`**:
```python
"""Repo analysis — pure business logic."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.db.connection import get_db
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.main_graph.subgraphs.ingestion_subgraphs.repo.dao import RepoCacheDAO
from src.main_graph.subgraphs.ingestion_subgraphs.repo.models import (
    Issue,
    Release,
    RepoCacheEntry,
    RepoEntry,
    Repository,
    Vulnerability,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.curators.issues import (
    make_issue_curation_agent,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.curators.releases import (
    make_release_curation_agent,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.curators.vulnerabilities import (
    make_vulnerability_curation_agent,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.state import RepoState
from src.main_graph.subgraphs.ingestion_subgraphs.sbom_utils import (
    get_vcs_url,
    parse_github_owner_repo,
)
from src.utils.config import settings
from src.utils.workers_client import ingest_and_wait

_log = logging.getLogger(__name__)


def _safe_float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


async def _read_workers_cache(collection: str, owner: str, repo: str) -> list[dict]:
    col = get_db()[collection]
    doc = await col.find_one({"name": f"{owner}/{repo}"})
    if doc is None:
        return []
    data = doc.get("items") or doc.get("data") or []
    return data if isinstance(data, list) else []


async def analyze_service(
    state: RepoState,
    dao: IngestionResultPort,
    cache_dao: RepoCacheDAO,
) -> dict:
    dep_name = state.get("dependency_name", "")
    sbom = state.get("sbom_cyclonedx", {})

    vcs_url = get_vcs_url(sbom, dep_name) if dep_name else None
    parsed = parse_github_owner_repo(vcs_url) if vcs_url else None

    if not parsed:
        _log.warning("repo: no GitHub VCS URL in SBOM for %s", dep_name)
        result_id = await dao.save(RepoEntry(repositories=[]))
        return {"result_id": result_id}

    owner, name = parsed
    url = vcs_url or ""

    cached = await cache_dao.find_cached_entry(owner, name, settings.lookback_days, settings.repo_cache_max_age_days)
    if cached is not None:
        result_id = await dao.save(cached.entry)
        _log.info("repo: cache hit for %s/%s, result_id=%s", owner, name, result_id)
        return {"result_id": result_id}

    try:
        await ingest_and_wait(entity_types=["github_issues", "github_releases", "github_advisories"], items=[f"{owner}/{name}"])
    except Exception as exc:
        _log.warning("repo: workers ingest failed for %s/%s: %s", owner, name, exc)
        result_id = await dao.save(RepoEntry(repositories=[]))
        return {"result_id": result_id}

    raw_issues = await _read_workers_cache("github_issues_cache", owner, name)
    raw_releases = await _read_workers_cache("github_releases_cache", owner, name)
    raw_vulns = await _read_workers_cache("github_advisories_cache", owner, name)

    batch_size = settings.reviewer_batch_size

    try:
        curated_issues = await make_issue_curation_agent().curate(raw_issues, batch_size)
    except Exception as exc:
        _log.warning("repo: issue curation failed: %s", exc)
        curated_issues = raw_issues

    try:
        curated_releases = await make_release_curation_agent().curate(raw_releases, batch_size)
    except Exception as exc:
        _log.warning("repo: release curation failed: %s", exc)
        curated_releases = raw_releases

    try:
        curated_vulns = await make_vulnerability_curation_agent().curate(raw_vulns, batch_size)
    except Exception as exc:
        _log.warning("repo: vuln curation failed: %s", exc)
        curated_vulns = raw_vulns

    issues = [
        Issue(
            id=str(i.get("number", "")),
            title=i.get("standardized_title") or i.get("title", ""),
            state=i.get("state", "open"),
            created_at=i.get("created_at"),
            body=(i.get("body") or "")[:1000] or None,
            type=i.get("type"),
            summary=i.get("summary"),
            updated_at=i.get("updated_at"),
            closed_at=i.get("closed_at"),
            labels=[lbl.get("name", "") if isinstance(lbl, dict) else str(lbl) for lbl in (i.get("labels") or [])],
        )
        for i in curated_issues
        if i.get("number") is not None
    ]
    releases = [
        Release(
            tag=r.get("tag_name") or str(r.get("id", "")),
            name=r.get("standardized_title") or r.get("name"),
            published_at=r.get("published_at"),
            body=(r.get("body") or "")[:2000] or None,
            release_type=r.get("release_type"),
            change_summary=r.get("change_summary"),
        )
        for r in curated_releases
    ]
    vulnerabilities = [
        Vulnerability(
            id=v.get("ghsa_id", ""),
            severity=v.get("severity_category", "unknown"),
            description=v.get("summary"),
            cve_id=v.get("cve_id"),
            affected_components=v.get("affected_components") or [],
            published_at=v.get("published_at"),
            cvss_score=_safe_float((v.get("cvss") or {}).get("score") if isinstance(v.get("cvss"), dict) else v.get("cvss_score")),
            cwe_ids=[c.get("cwe_id", "") if isinstance(c, dict) else str(c) for c in (v.get("cwes") or [])],
        )
        for v in curated_vulns
        if v.get("ghsa_id")
    ]

    repository = Repository(url=url, owner=owner, name=name, issues=issues, releases=releases, vulnerabilities=vulnerabilities)
    entry = RepoEntry(repositories=[repository])

    try:
        await cache_dao.upsert_cached_entry(RepoCacheEntry(owner=owner, repo_name=name, lookback_days=settings.lookback_days, fetched_at=datetime.now(UTC), entry=entry))
    except Exception:
        _log.warning("repo: cache write failed for %s/%s", owner, name)

    result_id = await dao.save(entry)
    _log.info("repo: saved — issues=%d releases=%d vulns=%d result_id=%s", len(issues), len(releases), len(vulnerabilities), result_id)
    return {"result_id": result_id}
```

- [ ] **Thin out `src/main_graph/subgraphs/ingestion_subgraphs/repo/nodes/analyze.py`**:
```python
"""Repo analysis node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.ingestion_subgraphs.repo.service import analyze_service
from src.main_graph.subgraphs.ingestion_subgraphs.repo.state import RepoState


async def analyze(state: RepoState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await analyze_service(
        state,
        svc["ingestion_daos"]["repo"],
        svc["repo_cache_dao"],
    )
```

- [ ] **Verify**
```bash
cd apps/backend && uv run python -c "from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.analyze import analyze; print('ok')"
```
Expected: `ok`

- [ ] **Commit**
```bash
git add apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/
git commit -m "refactor: repo — service.py + thin node, inject cache DAO"
```

---

## Task 11: Runtime — service + thin node

**Files:**
- Create: `src/main_graph/subgraphs/ingestion_subgraphs/runtime/service.py`
- Modify: `src/main_graph/subgraphs/ingestion_subgraphs/runtime/nodes/analyze.py`

Note: `runtime/analyze.py` uses `run_docker_install` and `run_docker_script` — sync LangChain tools using `subprocess.run`. These tools use different Docker flags (resource limits, working directory) from the general `ContainerRunPort`. They remain as-is in `runtime/tools/docker_tools.py` — the service.py imports them directly. The purity test targets node files, not service files.

- [ ] **Create `src/main_graph/subgraphs/ingestion_subgraphs/runtime/service.py`**:
```python
"""Runtime analysis — pure business logic."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime

from langchain_core.messages import ToolMessage

from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.dao import RuntimeCacheDAO
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.models import (
    LintResult,
    RuntimeCacheEntry,
    RuntimeEntry,
    TestResult,
)
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.state import RuntimeState
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.tools.dependency_tools import (
    clone_github_repo,
    read_package_json,
)
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.tools.docker_tools import (
    run_docker_install,
    run_docker_script,
)
from src.main_graph.subgraphs.ingestion_subgraphs.sbom_utils import (
    get_component_version,
    get_vcs_url,
)
from src.utils.config import settings
from src.utils.llm import Model, get_llm, parse_llm_json

_log = logging.getLogger(__name__)
_llm = get_llm(Model.GPT_4O_MINI)

_DOWNLOAD_TOOLS = [clone_github_repo]
_IDENTIFY_TOOLS = [read_package_json]
_EXECUTE_TOOLS = [run_docker_install, run_docker_script]

_DOWNLOAD_PROMPT = """\
You are a tool-calling agent that downloads npm package source code from GitHub.
Given a package name, version, and repository URL, call clone_github_repo with
the repository URL, version, and destination directory.
Return a JSON object with keys: resolved_version, error.
If cloning fails, set error to the error message and resolved_version to null.\
"""

_IDENTIFY_PROMPT = """\
You are a code quality script classifier for npm packages.
Given a package directory, read package.json and identify AT MOST 3 quality scripts.

INCLUDE: test, test:*, tests, audit, check, check:*, validate, verify
EXCLUDE: start, serve, dev, watch, build, bundle, compile, deploy, publish,
         install, docs, storybook, format, fmt, lint, typecheck, tsc

Return ONLY a JSON object: { "quality_scripts": ["name1", "name2"] }\
"""

_EXECUTE_PROMPT = """\
You are a tool-calling agent that executes npm scripts inside Docker containers.
1. Call run_docker_install once to install dependencies.
2. For each script, call run_docker_script to execute it.
   Continue to the next script even if one fails.

Return a JSON object:
{
  "results": {
    "script-name": {
      "script_name": "script-name",
      "exit_code": 0, "stdout": "...", "stderr": "...",
      "duration_seconds": 1.23, "timed_out": false
    }
  },
  "error": null
}\
"""


@dataclass
class _ScriptResult:
    script_name: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = field(default=False)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


async def _run_agent(llm_with_tools, tools, system_prompt: str, user_msg: str, max_turns: int) -> dict | None:
    tool_map = {t.name: t for t in tools}
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_msg}]
    for _ in range(max_turns):
        response = await llm_with_tools.ainvoke(messages)
        messages.append(response)
        if not response.tool_calls:
            try:
                return parse_llm_json(response.content or "")
            except Exception:
                return None
        for tc in response.tool_calls:
            tool_fn = tool_map.get(tc["name"])
            if tool_fn:
                if hasattr(tool_fn, "ainvoke"):
                    tool_result = await tool_fn.ainvoke(tc["args"])
                else:
                    tool_result = tool_fn.invoke(tc["args"])
                messages.append(ToolMessage(content=str(tool_result), tool_call_id=tc["id"]))
    return None


def _map_to_runtime_entry(results: dict[str, _ScriptResult]) -> tuple[TestResult | None, LintResult | None]:
    test_scripts = [r for name, r in results.items() if name.startswith("test") or name == "tests"]
    lint_scripts = [r for name, r in results.items() if name.startswith("lint")]
    test_result = None
    if test_scripts:
        passed = sum(1 for r in test_scripts if r.passed)
        failed = len(test_scripts) - passed
        errors = [r.stderr[:300] for r in test_scripts if not r.passed and r.stderr]
        test_result = TestResult(passed=passed, failed=failed, errors=errors)
    lint_result = None
    if lint_scripts:
        errors_count = sum(1 for r in lint_scripts if not r.passed)
        lint_result = LintResult(errors=errors_count)
    return test_result, lint_result


async def analyze_service(
    state: RuntimeState,
    dao: IngestionResultPort,
    cache_dao: RuntimeCacheDAO,
) -> dict:
    dep_name = state.get("dependency_name", "")
    if not dep_name:
        result_id = await dao.save(RuntimeEntry())
        return {"result_id": result_id}

    sbom = state.get("sbom_cyclonedx", {})
    version_spec = (get_component_version(sbom, dep_name) or "").lstrip("^~>=< ")
    repository_url = get_vcs_url(sbom, dep_name) or ""

    if not repository_url:
        _log.warning("runtime: no repository_url — saving empty entry")
        result_id = await dao.save(RuntimeEntry())
        return {"result_id": result_id}

    cached = await cache_dao.find_cached_entry(dep_name, version_spec, settings.runtime_cache_max_age_days)
    if cached is not None:
        result_id = await dao.save(cached.entry)
        _log.info("runtime: cache hit for %s@%s, result_id=%s", dep_name, version_spec, result_id)
        return {"result_id": result_id}

    base_tmp = os.path.expanduser("~/tmp")
    os.makedirs(base_tmp, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="npm_qa_", dir=base_tmp)

    try:
        download_llm = _llm.bind_tools(_DOWNLOAD_TOOLS)
        identify_llm = _llm.bind_tools(_IDENTIFY_TOOLS)
        execute_llm = _llm.bind_tools(_EXECUTE_TOOLS)

        download_result = await _run_agent(download_llm, _DOWNLOAD_TOOLS, _DOWNLOAD_PROMPT, f"Download '{dep_name}' version '{version_spec}' from '{repository_url}' into '{tmp_dir}'.", max_turns=6)
        if not download_result or download_result.get("error"):
            err = (download_result or {}).get("error", "download agent did not converge")
            _log.warning("runtime: download failed: %s", err)
            result_id = await dao.save(RuntimeEntry())
            return {"result_id": result_id}

        identify_result = await _run_agent(identify_llm, _IDENTIFY_TOOLS, _IDENTIFY_PROMPT, f"Read package.json from '{tmp_dir}' and identify quality scripts.", max_turns=4)
        script_names: list[str] = (identify_result or {}).get("quality_scripts", [])

        if not script_names:
            _log.info("runtime: no quality scripts identified")
            result_id = await dao.save(RuntimeEntry())
            return {"result_id": result_id}

        script_list = ", ".join(f"'{s}'" for s in script_names)
        execute_result = await _run_agent(execute_llm, _EXECUTE_TOOLS, _EXECUTE_PROMPT, f"Package directory: '{tmp_dir}'.\nQuality scripts to run: [{script_list}].\nDocker image: '{settings.node_docker_image}', memory: '{settings.docker_memory_limit}', cpu: {settings.docker_cpu_limit}, timeout per script: {settings.script_timeout_seconds}s.", max_turns=len(script_names) + 6)

        results: dict[str, _ScriptResult] = {}
        if execute_result:
            for name, raw in (execute_result.get("results") or {}).items():
                results[name] = _ScriptResult(script_name=name, exit_code=raw.get("exit_code", -1), stdout=raw.get("stdout", ""), stderr=raw.get("stderr", ""), duration_seconds=raw.get("duration_seconds", 0.0), timed_out=raw.get("timed_out", False))

        test_result, lint_result = _map_to_runtime_entry(results)
        entry = RuntimeEntry(test_results=test_result, lint_results=lint_result)

        try:
            await cache_dao.upsert_cached_entry(RuntimeCacheEntry(package_name=dep_name, package_version=version_spec, fetched_at=datetime.now(UTC), entry=entry))
        except Exception:
            _log.warning("runtime: cache write failed for %s@%s", dep_name, version_spec)

        result_id = await dao.save(entry)
        _log.info("runtime: saved result_id=%s scripts_run=%d", result_id, len(results))
        return {"result_id": result_id}

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Thin out `src/main_graph/subgraphs/ingestion_subgraphs/runtime/nodes/analyze.py`**:
```python
"""Runtime analysis node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.service import analyze_service
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.state import RuntimeState


async def analyze(state: RuntimeState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await analyze_service(
        state,
        svc["ingestion_daos"]["runtime"],
        svc["runtime_cache_dao"],
    )
```

- [ ] **Verify**
```bash
cd apps/backend && uv run python -c "from src.main_graph.subgraphs.ingestion_subgraphs.runtime.nodes.analyze import analyze; print('ok')"
```
Expected: `ok`

- [ ] **Commit**
```bash
git add apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/
git commit -m "refactor: runtime — service.py + thin node, inject cache DAO"
```

---

## Task 12: Impact — service + thin node

**Files:**
- Create: `src/main_graph/subgraphs/ingestion_subgraphs/impact/service.py`
- Modify: `src/main_graph/subgraphs/ingestion_subgraphs/impact/nodes/analyze.py`

- [ ] **Create `src/main_graph/subgraphs/ingestion_subgraphs/impact/service.py`**:
```python
"""Impact analysis — pure business logic."""

from __future__ import annotations

import json
import logging

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.main_graph.subgraphs.ingestion_subgraphs.impact.models import ImpactEntry
from src.main_graph.subgraphs.ingestion_subgraphs.impact.state import ImpactState
from src.main_graph.subgraphs.ingestion_subgraphs.impact.tools.filesystem import (
    find_usages,
    list_source_files,
    read_file_excerpt,
)
from src.main_graph.subgraphs.ingestion_subgraphs.impact.tools.sbom_tools import (
    compute_blast_radius,
    compute_direct_dependents,
)
from src.utils.llm import Model, get_llm

_log = logging.getLogger(__name__)
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM_PROMPT = """\
You are an impact analysis agent for JavaScript/TypeScript projects.

Your task: analyze how the dependency '{dep_name}' is used inside the project
located at '{repo_path}'.

Steps:
1. Use list_source_files to enumerate all source files.
2. Use find_usages to find all import/require statements for '{dep_name}'.
3. Use read_file_excerpt to read up to 10 representative usage sites and
   identify which parts of the API are used.
4. Use get_direct_dependents and get_blast_radius to understand the
   transitive impact if this dep changes.
5. Write a concise usage_summary (2-3 sentences) and blast_radius_summary
   (1-2 sentences).

Return a structured result with all ImpactEntry fields populated.
"""


async def analyze_service(state: ImpactState, dao: IngestionResultPort) -> dict:
    dep_name = state.get("dependency_name", "")
    repo_path = state.get("repo_path", "")

    if not dep_name or not repo_path:
        result_id = await dao.save(ImpactEntry(dep_name=dep_name))
        return {"result_id": result_id}

    sbom = state.get("sbom_cyclonedx", {})

    @tool
    def get_direct_dependents(target: str) -> str:
        """Return package names that directly depend on target in the SBOM."""
        return json.dumps(compute_direct_dependents(target, sbom))

    @tool
    def get_blast_radius(target: str) -> str:
        """Compute blast radius for target. Returns JSON with keys: direct_dependents, transitive_dependents, max_depth."""
        return json.dumps(compute_blast_radius(target, sbom))

    tools = [list_source_files, find_usages, read_file_excerpt, get_direct_dependents, get_blast_radius]

    try:
        agent = create_agent(model=_llm, tools=tools, response_format=ImpactEntry)
        result = await agent.ainvoke(
            {"messages": [
                SystemMessage(content=_SYSTEM_PROMPT.format(dep_name=dep_name, repo_path=repo_path)),
                HumanMessage(content=f"Analyze the impact of '{dep_name}' now."),
            ]},
            config={"recursion_limit": 30},
        )
        entry: ImpactEntry = result["structured_response"]
        entry.dep_name = dep_name
    except Exception:
        _log.exception("impact: agent failed for dep=%s", dep_name)
        entry = ImpactEntry(dep_name=dep_name)

    result_id = await dao.save(entry)
    _log.info("impact: saved result_id=%s dep=%s", result_id, dep_name)
    return {"result_id": result_id}
```

- [ ] **Thin out `src/main_graph/subgraphs/ingestion_subgraphs/impact/nodes/analyze.py`**:
```python
"""Impact analysis node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.ingestion_subgraphs.impact.service import analyze_service
from src.main_graph.subgraphs.ingestion_subgraphs.impact.state import ImpactState


async def analyze(state: ImpactState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await analyze_service(state, svc["ingestion_daos"]["impact"])
```

- [ ] **Verify**
```bash
cd apps/backend && uv run python -c "from src.main_graph.subgraphs.ingestion_subgraphs.impact.nodes.analyze import analyze; print('ok')"
```
Expected: `ok`

- [ ] **Commit**
```bash
git add apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/impact/
git commit -m "refactor: impact — service.py + thin node"
```

---

## Task 13: Discovery — service + thin nodes

**Files:**
- Create: `src/main_graph/subgraphs/discovery/service.py`
- Modify: `src/main_graph/subgraphs/discovery/nodes/clone_repository.py`
- Modify: `src/main_graph/subgraphs/discovery/nodes/generate_sbom.py`
- Modify: `src/main_graph/subgraphs/discovery/nodes/lock_generator_agent.py`

Note: `inspector_agent.py` and `build_dependency_summary.py` use only LLM — no infrastructure ports needed, left unchanged.

`generate_sbom.py` currently imports `run_docker_command` from `lock_generator_agent.py` — after refactoring both nodes get it from `config["configurable"]["docker_tool"]`.

- [ ] **Create `src/main_graph/subgraphs/discovery/service.py`**:
```python
"""Discovery subgraph — pure business logic for infrastructure-touching nodes."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.main_graph.subgraphs.discovery.models import SbomEntry
from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.utils.trivy import run_trivy

logger = logging.getLogger(__name__)

_MIN_NODE_VERSION = 20
_MANIFESTS = ("package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json")


def _create_tmp_dir(job_id: str) -> str:
    tmp_dir = os.path.abspath(f"tmp/debug_job_{job_id}")
    os.makedirs(tmp_dir, exist_ok=True)
    return tmp_dir


async def clone_repository_service(state: DiscoveryState, container: ContainerRunPort) -> dict:
    repo_url = state.get("repo_url", "").strip()
    if not repo_url:
        return {"discovery_error": "No repository URL provided"}

    tmp_dir = _create_tmp_dir(state["job_id"])
    image = "alpine/git"
    volume = f"{tmp_dir}:/workspace"
    command = f"git clone --depth=1 --single-branch {repo_url} /workspace"
    logger.info("clone_repository: cloning %s into %s", repo_url, tmp_dir)

    returncode, _stdout, stderr = await container.run(image, command, volume)
    if returncode != 0:
        logger.error("clone_repository: clone failed: %s", stderr[:300])
        return {"discovery_error": f"git clone failed: {stderr[:300]}"}

    logger.info("clone_repository: cloned %s", repo_url)
    return {"repo_path": tmp_dir}


def _node_version(image: str) -> int | None:
    match = re.match(r"node:(\d+)", image)
    return int(match.group(1)) if match else None


def _detect_manifest_files(repo_path: str) -> list[str]:
    root = Path(repo_path)
    return [name for name in _MANIFESTS if (root / name).exists()]


async def generate_sbom_service(
    state: DiscoveryState,
    container: ContainerRunPort,
    sbom_dao: IngestionResultPort,
) -> dict:
    repo_path = state.get("repo_path", "")
    repo_url = state.get("repo_url", "")

    if not repo_path:
        logger.error("generate_sbom: no repo_path in state")
        entry = SbomEntry(repo_url=repo_url, scan_error="repo_path not available")
        result_id = await sbom_dao.save(entry)
        return {"sbom_cyclonedx": {}, "sbom_result_id": result_id, "manifest_files": [], "sbom_error": "repo_path not available"}

    pm = state.get("detected_package_manager", "npm")
    docker_image = state.get("docker_image", "node:lts-alpine")

    version = _node_version(docker_image)
    sbom_data: dict = {}
    sbom_error: str | None = None

    if version is not None and version < _MIN_NODE_VERSION:
        sbom_error = f"Node.js {version} does not support '{pm} sbom' (requires node:{_MIN_NODE_VERSION}+)"
    else:
        command = f"{pm} sbom --sbom-format=cyclonedx --package-lock-only"
        logger.info("generate_sbom: running '%s' in %s", command, docker_image)
        volume = f"{repo_path}:/workspace"
        returncode, stdout, stderr = await container.run(docker_image, command, volume)
        if returncode != 0:
            sbom_error = stderr or "sbom command failed with no stderr"
        else:
            try:
                sbom_data = json.loads(stdout)
            except json.JSONDecodeError as exc:
                sbom_error = f"sbom output is not valid JSON: {exc}"

    if sbom_error:
        logger.error("generate_sbom: %s", sbom_error)

    manifest_files = _detect_manifest_files(repo_path)
    entry = SbomEntry(repo_url=repo_url, sbom_cyclonedx=sbom_data, scan_error=sbom_error)
    result_id = await sbom_dao.save(entry)
    logger.info("generate_sbom: saved — result_id=%s error=%s", result_id, sbom_error)

    result: dict = {"sbom_cyclonedx": sbom_data, "sbom_result_id": result_id, "manifest_files": manifest_files}
    if sbom_error:
        result["sbom_error"] = sbom_error
    return result
```

- [ ] **Thin out `src/main_graph/subgraphs/discovery/nodes/clone_repository.py`**:
```python
"""Node: clone_repository."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.service import clone_repository_service
from src.main_graph.subgraphs.discovery.state import DiscoveryState


async def clone_repository(state: DiscoveryState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await clone_repository_service(state, svc["container"])
```

- [ ] **Thin out `src/main_graph/subgraphs/discovery/nodes/generate_sbom.py`**:
```python
"""Node: generate_sbom."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.service import generate_sbom_service
from src.main_graph.subgraphs.discovery.state import DiscoveryState


async def generate_sbom(state: DiscoveryState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await generate_sbom_service(state, svc["container"], svc["sbom_dao"])
```

- [ ] **Thin out `src/main_graph/subgraphs/discovery/nodes/lock_generator_agent.py`** — receives `docker_tool` from config instead of importing it:
```python
"""Node: lock_generator_agent."""

import logging

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.main_graph.subgraphs.discovery.tools.filesystem import read_file, write_file
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)
_llm = get_llm(Model.GPT_5_4_MINI)


class LockGenResult(BaseModel):
    success: bool
    attempts: int
    error: str | None


_SYSTEM_TEMPLATE = """\
You are generating a lock file for a Node.js project \
located at {repo_path} using {pm} in {image}.

Your goal is to successfully produce a valid lock file (package-lock.json, yarn.lock, \
    or pnpm-lock.yaml depending on the package manager).

Process:
- Use run_docker_command to install dependencies in the workspace.
- If the command fails, inspect the error output and decide the most appropriate fix.
-You may:
    - adjust Node image version
    - patch package.json when necessary
- Re-run after each fix (up to 6 attempts).

After each attempt:
- verify whether the expected lock file exists using read_file.

Stop when:
- the lock file is successfully generated and readable

Return:

success
lock_file_path
final_error (if any)
attempts
"""


async def lock_generator_agent(state: DiscoveryState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    docker_tool = svc["docker_tool"]

    repo_path = state.get("repo_path")
    pm = state.get("detected_package_manager", "npm")
    image = state.get("docker_image", "node:lts-alpine")
    command = state.get("install_command", "npm install")

    agent = create_agent(
        model=_llm,
        tools=[docker_tool, read_file, write_file],
        response_format=LockGenResult,
    )

    try:
        result = await agent.ainvoke(
            {"messages": [
                SystemMessage(content=_SYSTEM_TEMPLATE.format(repo_path=repo_path, pm=pm, image=image, command=command)),
                HumanMessage(content="Generate the lock file now."),
            ]},
            config={"recursion_limit": 25},
        )
        output: LockGenResult = result["structured_response"]
    except Exception as exc:
        logger.exception("lock_generator_agent: failed")
        return {"lock_generation_attempts": 0, "lock_generation_error": f"Lock generator agent failed: {exc}"}

    return {
        "lock_generation_attempts": output.attempts,
        "lock_generation_error": output.error if not output.success else None,
    }
```

- [ ] **Verify**
```bash
cd apps/backend && uv run python -c "
from src.main_graph.subgraphs.discovery.nodes.clone_repository import clone_repository
from src.main_graph.subgraphs.discovery.nodes.generate_sbom import generate_sbom
from src.main_graph.subgraphs.discovery.nodes.lock_generator_agent import lock_generator_agent
print('ok')
"
```
Expected: `ok`

- [ ] **Commit**
```bash
git add apps/backend/src/main_graph/subgraphs/discovery/
git commit -m "refactor: discovery — service.py + thin nodes for clone, sbom, lock_generator"
```

---

## Task 14: Orchestrator — service + thin node

**Files:**
- Create: `src/main_graph/nodes/orchestrator_service.py`
- Modify: `src/main_graph/nodes/orchestrator.py`

- [ ] **Create `src/main_graph/nodes/orchestrator_service.py`**:
```python
"""Orchestrator business logic — plan presentation, intent classification, vector store writes."""

import logging
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END
from langgraph.types import Command, interrupt

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.domain.ports.vector_store_port import VectorStorePort
from src.main_graph.nodes.planner import _PIPELINE_SUBGRAPHS, run_planner
from src.main_graph.plan import Plan
from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_DESCRIPTIONS
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_SUBGRAPH_DESC: dict[str, str] = {}
for _entry in SUBGRAPH_DESCRIPTIONS:
    _name, _desc = _entry.split(":", 1)
    _SUBGRAPH_DESC[_name.strip()] = _desc.strip()
for _name, _desc in _PIPELINE_SUBGRAPHS:
    _SUBGRAPH_DESC[_name] = _desc

_INTENT_SYSTEM_PROMPT = """\
You are classifying a user's response to a proposed dependency analysis plan.
The user was shown the plan and asked whether to proceed, request changes, or cancel.

Classify their intent as exactly one of:
  - approve: user is satisfied and wants to proceed
  - change: user wants modifications, has concerns, or provides new instructions
  - cancel: user wants to abort the analysis entirely

Return ONLY one word: approve, change, or cancel.
"""


def _present_plan(plan: Plan) -> str:
    subgraphs = plan.get("subgraphs", []) if isinstance(plan, dict) else list(plan)
    dep_filter = plan.get("dep_filter") if isinstance(plan, dict) else None
    lines = ["**Proposed Analysis Plan:**\n"]
    for i, name in enumerate(subgraphs, 1):
        desc = _SUBGRAPH_DESC.get(name, name)
        lines.append(f"{i}. **{name}**: {desc}")
    if dep_filter:
        lines.append(f"\n**Scope:** {', '.join(dep_filter)}")
    lines.append("\nWould you like to proceed with this plan, request changes, or cancel?")
    return "\n".join(lines)


async def _classify_intent(plan: Plan, user_input: str) -> str:
    subgraphs = plan.get("subgraphs", []) if isinstance(plan, dict) else list(plan)
    plan_str = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(subgraphs))
    response = await _llm.ainvoke([
        {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
        {"role": "user", "content": f"Plan:\n{plan_str}\n\nUser message: {user_input}"},
    ])
    intent = response.content.strip().lower()
    if intent not in ("approve", "change", "cancel"):
        logger.warning("orchestrator: unexpected intent %r, defaulting to 'change'", intent)
        intent = "change"
    return intent


async def orchestrator_service(
    state: MainState,
    dao: JobRepositoryPort,
    vector_store: VectorStorePort,
) -> dict | Command:
    job_id = state["job_id"]
    plan = await run_planner(state)

    while True:
        assistant_msg = _present_plan(plan)
        proposal_created_at = datetime.now(UTC).isoformat()
        await dao.push_proposal(job_id, {"created_at": proposal_created_at, "plan": plan, "assistant_message": assistant_msg})

        user_input: str = interrupt({
            "plan": plan,
            "assistant_message": assistant_msg,
            "discovery_summary": state.get("discovery_summary", ""),
            "components_count": len(state.get("sbom_cyclonedx", {}).get("components", [])),
        })

        try:
            await vector_store.add_texts([f"Assistant: {assistant_msg}", f"User: {user_input}"])
        except Exception:
            logger.warning("orchestrator: failed to add messages to vector store")

        intent = await _classify_intent(plan, user_input)
        logger.info("orchestrator: job=%s intent=%r plan=%s", job_id, intent, plan)

        await dao.update_proposal(job_id, created_at=proposal_created_at, user_response=user_input, intent=intent)

        new_messages = [AIMessage(content=assistant_msg), HumanMessage(content=user_input)]

        if intent == "approve":
            new_messages.append(AIMessage(content="Plan approved! Execution is starting now. You will be redirected to the execution detail page shortly."))
            return {"plan": plan, "messages": new_messages}

        if intent == "cancel":
            return Command(goto=END, update={"plan": [], "messages": new_messages, "cancelled": True})

        plan = await run_planner(state, extra_instructions=user_input)
```

- [ ] **Thin out `src/main_graph/nodes/orchestrator.py`**:
```python
"""Orchestrator node."""

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from src.main_graph.config import get_services
from src.main_graph.nodes.orchestrator_service import orchestrator_service
from src.main_graph.state import MainState


async def orchestrator(state: MainState, config: RunnableConfig) -> dict | Command:
    svc = get_services(config)
    return await orchestrator_service(state, svc["job_repo"], svc["vector_store"])
```

- [ ] **Verify**
```bash
cd apps/backend && uv run python -c "from src.main_graph.nodes.orchestrator import orchestrator; print('ok')"
```
Expected: `ok`

- [ ] **Commit**
```bash
git add apps/backend/src/main_graph/nodes/orchestrator.py apps/backend/src/main_graph/nodes/orchestrator_service.py
git commit -m "refactor: orchestrator — service + thin node, inject JobRepositoryPort + VectorStorePort"
```

---

## Task 15: Execute plan — service + thin node + remove `SUBGRAPH_DAOS`

**Files:**
- Create: `src/main_graph/nodes/execute_plan_service.py`
- Modify: `src/main_graph/nodes/execute_plan.py`
- Modify: `src/main_graph/subgraphs/ingestion_subgraphs/__init__.py`

- [ ] **Create `src/main_graph/nodes/execute_plan_service.py`**:
```python
"""Execute plan business logic."""

import logging

from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_REGISTRY

logger = logging.getLogger(__name__)


async def execute_plan_service(
    state: MainState,
    dao: JobRepositoryPort,
    ingestion_daos: dict[str, IngestionResultPort],
    config_dict: dict,
) -> dict:
    name = state.get("subgraph_name", "")
    dep_name: str | None = state.get("dep_name")
    job_id = state.get("job_id", "")

    subgraph = SUBGRAPH_REGISTRY.get(name)

    if subgraph is None:
        logger.warning("execute_plan: unknown subgraph %r", name)
        if job_id:
            await dao.complete_artifact(job_id, name, "failed")
        return {"subgraph_results": [{"subgraph": name, "dep_name": dep_name, "error": "unknown subgraph"}]}

    artifact_key = f"{name}:{dep_name}" if dep_name else name
    if job_id:
        await dao.start_artifact(job_id, artifact_key)

    try:
        hydrated_upstream = {}
        for sg, result_id in state.get("upstream_results", {}).items():
            output_dao = ingestion_daos.get(sg)
            if output_dao and result_id:
                data = await output_dao.get(result_id)
                if data:
                    hydrated_upstream[sg] = data

        invocation: dict = {
            "sbom_cyclonedx": state.get("sbom_cyclonedx", {}),
            "discovery_summary": state.get("discovery_summary", ""),
            "concern": state.get("concern", ""),
            "upstream_results": hydrated_upstream,
        }
        if repo_path := state.get("repo_path"):
            invocation["repo_path"] = repo_path
        if dep_name:
            invocation["dependency_name"] = dep_name

        # Pass the full configurable so injected nodes inside the subgraph
        # can access their ports via get_services(config).
        from langchain_core.runnables import RunnableConfig
        subgraph_config: RunnableConfig = {"configurable": {
            **config_dict,
            # subgraphs use their own thread_id namespace
            "thread_id": f"{job_id}:{name}:{dep_name or 'all'}",
        }}
        result = await subgraph.ainvoke(invocation, subgraph_config)

        result_id = result.get("result_id")
        if job_id:
            await dao.update_artifact_data(job_id, artifact_key, {"result_id": result_id})
            await dao.complete_artifact(job_id, artifact_key, "done")
        logger.info("execute_plan: %s(%s) completed, result_id=%s", name, dep_name, result_id)
        return {"subgraph_results": [{"subgraph": name, "dep_name": dep_name, "result_id": result_id}]}
    except Exception:
        logger.exception("execute_plan: %s(%s) failed", name, dep_name)
        if job_id:
            await dao.complete_artifact(job_id, artifact_key, "failed")
        raise
```

- [ ] **Thin out `src/main_graph/nodes/execute_plan.py`**:
```python
"""Execute plan node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.nodes.execute_plan_service import execute_plan_service
from src.main_graph.state import MainState


async def execute_plan(state: MainState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await execute_plan_service(
        state,
        svc["job_repo"],
        svc["ingestion_daos"],
        dict(svc),
    )
```

- [ ] **Remove `SUBGRAPH_DAOS` from `src/main_graph/subgraphs/ingestion_subgraphs/__init__.py`**:
```python
from src.main_graph.subgraphs.ingestion_subgraphs import (
    impact,
    license_compliance,
    registry,
    repo,
    runtime,
    vulnerabilities,
)

_MODULES = [vulnerabilities, license_compliance, registry, repo, runtime, impact]

SUBGRAPH_REGISTRY = {mod.GRAPH_NAME: mod.subgraph for mod in _MODULES}
SUBGRAPH_DESCRIPTIONS = [mod.describe() for mod in _MODULES]
SUBGRAPH_DEPENDENCIES: dict[str, list[str]] = {
    mod.GRAPH_NAME: mod.DEPENDS_ON for mod in _MODULES
}

__all__ = [
    "SUBGRAPH_REGISTRY",
    "SUBGRAPH_DESCRIPTIONS",
    "SUBGRAPH_DEPENDENCIES",
]
```

- [ ] **Verify**
```bash
cd apps/backend && uv run python -c "
from src.main_graph.nodes.execute_plan import execute_plan
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_REGISTRY, SUBGRAPH_DESCRIPTIONS
print('ok')
"
```
Expected: `ok`

- [ ] **Commit**
```bash
git add apps/backend/src/main_graph/nodes/execute_plan.py
git add apps/backend/src/main_graph/nodes/execute_plan_service.py
git add apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py
git commit -m "refactor: execute_plan — service + thin node, remove SUBGRAPH_DAOS"
```

---

## Task 16: Architecture purity tests

**Files:**
- Create: `tests/architecture/__init__.py`
- Create: `tests/architecture/test_boundaries.py`

- [ ] **Create `tests/architecture/__init__.py`** (empty)

- [ ] **Create `tests/architecture/test_boundaries.py`**:
```python
"""Architecture boundary enforcement — static import checks."""

import ast
from pathlib import Path

_SRC = Path(__file__).parents[2] / "apps" / "backend" / "src"

_FORBIDDEN_IN_NODES = {
    "src.db.connection",
    "src.utils.trivy",
    "src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.dao",
    "src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.dao",
    "src.main_graph.subgraphs.ingestion_subgraphs.registry.dao",
    "src.main_graph.subgraphs.ingestion_subgraphs.repo.dao",
    "src.main_graph.subgraphs.ingestion_subgraphs.runtime.dao",
    "src.main_graph.subgraphs.ingestion_subgraphs.impact.dao",
    "src.main_graph.subgraphs.discovery.dao",
    "src.services.dependencies",
    "src.services.vector_store",
}

_FORBIDDEN_IN_DOMAIN_PORTS = {
    "langgraph",
    "motor",
    "pymongo",
    "src.services",
    "src.db",
}


def _get_imports(path: Path) -> set[str]:
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def _node_files():
    patterns = [
        "main_graph/nodes/*.py",
        "main_graph/subgraphs/*/nodes/*.py",
        "main_graph/subgraphs/ingestion_subgraphs/*/nodes/*.py",
    ]
    files = []
    for pattern in patterns:
        files.extend(_SRC.glob(pattern))
    return [f for f in files if f.name != "__init__.py"]


def test_nodes_do_not_import_concrete_infrastructure():
    """Node files must not import concrete DAOs, get_db, or trivy directly."""
    violations = []
    for node_file in _node_files():
        imports = _get_imports(node_file)
        bad = imports & _FORBIDDEN_IN_NODES
        if bad:
            violations.append(f"{node_file.relative_to(_SRC)}: {bad}")
    assert not violations, "Forbidden imports in node files:\n" + "\n".join(violations)


def test_domain_ports_have_no_infrastructure_imports():
    """domain/ports/*.py must not import infrastructure packages."""
    violations = []
    for port_file in (_SRC / "domain" / "ports").glob("*.py"):
        if port_file.name == "__init__.py":
            continue
        imports = _get_imports(port_file)
        for imp in imports:
            for forbidden in _FORBIDDEN_IN_DOMAIN_PORTS:
                if imp.startswith(forbidden):
                    violations.append(f"{port_file.name}: imports {imp}")
    assert not violations, "Forbidden imports in domain ports:\n" + "\n".join(violations)


def test_service_files_do_not_import_from_langgraph():
    """subgraph service.py files must not import from langgraph (orchestration belongs in nodes)."""
    violations = []
    service_files = list(_SRC.glob("main_graph/**/service.py")) + list(_SRC.glob("main_graph/nodes/*_service.py"))
    for svc_file in service_files:
        imports = _get_imports(svc_file)
        bad = {i for i in imports if i.startswith("langgraph")}
        if bad:
            violations.append(f"{svc_file.relative_to(_SRC)}: {bad}")
    assert not violations, "LangGraph imports in service files:\n" + "\n".join(violations)
```

- [ ] **Run tests — all should pass**
```bash
cd apps/backend && uv run pytest tests/architecture/ -v
```
Expected: `3 passed`

- [ ] **Run full test suite to check for regressions**
```bash
cd apps/backend && uv run pytest tests/ -v
```
Expected: all pass

- [ ] **Commit**
```bash
git add apps/backend/tests/architecture/
git commit -m "test: add architecture purity tests for boundary enforcement"
```
