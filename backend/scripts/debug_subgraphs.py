#!/usr/bin/env python3
"""Debug individual ingestion subgraph nodes with controlled inputs.

Usage (run from backend/):
    uv run python scripts/debug_subgraphs.py discovery
    uv run python scripts/debug_subgraphs.py registry
    uv run python scripts/debug_subgraphs.py repo
    uv run python scripts/debug_subgraphs.py runtime
    uv run python scripts/debug_subgraphs.py pipeline   # chains: registry → repo + runtime

Requires MongoDB running and API keys in .env.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

# ── Controlled inputs ─────────────────────────────────────────────────────────

REPO_URL = "https://github.com/jsynowiec/node-typescript-boilerplate"

DIRECT_DEPS = [
    {"name": "express", "version_spec": "^4.18.0"},
    {"name": "qs", "version_spec": "6.11.0"},
]

TRANSITIVE_DEPS = [
    {"name": "ms", "version_spec": "2.1.3"},
    {"name": "qs", "version_spec": "6.11.0"},
]
JOB_ID = str(int(time.time()))
CONCERN = "security"
DISCOVERY_SUMMARY = "Project uses Express 4 as its primary dependency."

# Canned registry result for repo/runtime isolated runs (bypasses a real registry call).
# Shape mirrors what registry_dao.get() returns.
CANNED_REGISTRY_UPSTREAM = {
    "repository_owner": "expressjs",
    "repository_name": "express",
    "repository_url": "https://github.com/expressjs/express",
    "packages": [
        {
            "name": "express",
            "current_version": "4.18.0",
            "latest_version": "4.21.2",
            "outdated": True,
            "deprecated": False,
            "vulnerabilities": [],
        }
    ],
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_section(title: str) -> None:
    width = 60
    print(f"\n{'─' * width}")
    print(f"  {title}")
    print(f"{'─' * width}")


def _dump(label: str, data: object) -> None:
    print(f"\n[{label}]")
    try:
        print(json.dumps(data, indent=2, default=str))
    except Exception:
        print(data)


def _base_state() -> dict:
    return {
        "direct_dependencies": DIRECT_DEPS,
        "transitive_dependencies": TRANSITIVE_DEPS,
        "concern": CONCERN,
        "discovery_summary": DISCOVERY_SUMMARY,
    }


# ── Discovery ─────────────────────────────────────────────────────────────────

async def debug_discovery() -> dict | None:
    """Run the full discovery subgraph and return the final state."""
    _print_section("DISCOVERY subgraph")

    from src.main_graph.subgraphs.discovery.graph import discovery_subgraph

    state = {"repo_url": REPO_URL, "concern": CONCERN, "job_id": JOB_ID}
    _dump("Input state", state)

    print("\n→ invoking discovery subgraph ...")
    result = await discovery_subgraph.ainvoke(state)
    _dump("Output state", dict(result))
    return dict(result)


# ── Registry ──────────────────────────────────────────────────────────────────

async def debug_registry() -> dict | None:
    """Run the registry analyze node and return the persisted entry."""
    _print_section("REGISTRY subgraph")

    from src.main_graph.subgraphs.ingestion_subgraphs.registry.dao import registry_dao
    from src.main_graph.subgraphs.ingestion_subgraphs.registry.nodes.analyze import (
        analyze,
    )

    state = _base_state()
    _dump("Input state", state)

    print("\n→ calling registry.analyze ...")
    result = await analyze(state)  # type: ignore[arg-type]
    _dump("Node output", result)

    result_id = result.get("result_id")
    if result_id:
        entry = await registry_dao.get(result_id)
        _dump(f"Persisted entry (result_id={result_id})", entry)
        return entry

    return None


# ── Repo ──────────────────────────────────────────────────────────────────────

async def debug_repo(registry_upstream: dict | None = None) -> dict | None:
    """Run the repo analyze node with real or canned registry upstream."""
    _print_section("REPO subgraph")

    from src.main_graph.subgraphs.ingestion_subgraphs.repo.dao import repo_dao
    from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.analyze import analyze

    upstream = registry_upstream or CANNED_REGISTRY_UPSTREAM
    state = {
        **_base_state(),
        "upstream_results": {"registry": upstream},
    }
    _dump("Input state (upstream_results.registry)", upstream)

    print("\n→ calling repo.analyze ...")
    result = await analyze(state)  # type: ignore[arg-type]
    _dump("Node output", result)

    result_id = result.get("result_id")
    if result_id:
        entry = await repo_dao.get(result_id)
        _dump(f"Persisted entry (result_id={result_id})", entry)
        return entry

    return None


# ── Runtime ───────────────────────────────────────────────────────────────────

async def debug_runtime(registry_upstream: dict | None = None) -> dict | None:
    """Run the runtime analyze node with real or canned registry upstream."""
    _print_section("RUNTIME subgraph")

    from src.main_graph.subgraphs.ingestion_subgraphs.runtime.dao import runtime_dao
    from src.main_graph.subgraphs.ingestion_subgraphs.runtime.nodes.analyze import (
        analyze,
    )

    upstream = registry_upstream or CANNED_REGISTRY_UPSTREAM
    state = {
        **_base_state(),
        "upstream_results": {"registry": upstream},
    }
    _dump(
        "Input state (upstream_results.registry.repository_url)",
        upstream.get("repository_url"),
    )

    print("\n→ calling runtime.analyze ...")
    result = await analyze(state)  # type: ignore[arg-type]
    _dump("Node output", result)

    result_id = result.get("result_id")
    if result_id:
        entry = await runtime_dao.get(result_id)
        _dump(f"Persisted entry (result_id={result_id})", entry)
        return entry

    return None


# ── Full pipeline ─────────────────────────────────────────────────────────────

async def debug_pipeline() -> None:
    """Chain registry → repo + runtime using real results as upstream."""
    _print_section("PIPELINE: registry → repo + runtime")

    # Stage 1: registry (no upstream deps)
    registry_entry = await debug_registry()

    # Stage 2: repo + runtime in parallel, both depend on registry output
    repo_task = asyncio.create_task(debug_repo(registry_entry))
    runtime_task = asyncio.create_task(debug_runtime(registry_entry))
    await asyncio.gather(repo_task, runtime_task)

    _print_section("PIPELINE complete")


# ── Entrypoint ────────────────────────────────────────────────────────────────

MODES = {
    "discovery": lambda: debug_discovery(),
    "registry": lambda: debug_registry(),
    "repo": lambda: debug_repo(),
    "runtime": lambda: debug_runtime(),
    "pipeline": debug_pipeline,
}

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "registry"
    if mode not in MODES:
        print(f"Unknown mode '{mode}'. Choose from: {', '.join(MODES)}")
        sys.exit(1)

    print(f"Running mode: {mode}")
    asyncio.run(MODES[mode]())
