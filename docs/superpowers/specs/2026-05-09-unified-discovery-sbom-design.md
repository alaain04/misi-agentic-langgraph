# Design: Unified Discovery + SBOM Subgraph

**Date:** 2026-05-09  
**Status:** Approved

---

## Problem

The discovery subgraph and the `sbom_gen` ingestion subgraph both detect dependencies:

- **Discovery** manually parses `package.json` and lock files (npm/yarn/pnpm) to produce `direct_dependencies`, `transitive_dependencies`, and `dependency_tree`.
- **SBOM Gen** runs Trivy via Docker on the same cloned repo to produce a CycloneDX SBOM with all components.

This is duplication. Trivy's CycloneDX output is a complete, standardized bill of materials that already contains every component with resolved versions — including the direct/transitive distinction via its `dependencies[].dependsOn` graph. The manual lock file parsers are a less-accurate reimplementation of what Trivy does natively.

Additionally, SBOM Gen runs as an ingestion subgraph (after the orchestrator plans), but the orchestrator needs dependency data to make its plan. This creates a sequencing mismatch.

---

## Goal

- Make the CycloneDX SBOM the single canonical dependency representation.
- Remove all manual lock file parsers and the separate `direct_dependencies` / `transitive_dependencies` / `dependency_tree` fields.
- Merge Trivy into discovery so the orchestrator has the full SBOM before planning.
- Delete the `sbom_gen` ingestion subgraph.
- `generate_sbom` only generates the SBOM (CycloneDX). Vulnerability and license analysis remain the responsibility of other ingestion subgraphs.

---

## Graph Topology

```
START
  └─► fetch_repository     (git clone → repo_path)
        │
        ├─► [discovery_error] ──────────────► build_dependency_summary
        │
        └─► generate_sbom                    (Trivy: CycloneDX only)
              │
              ├─► [sbom_error] ────────────► build_dependency_summary
              │
              └─► build_dependency_summary  (LLM summary from SBOM data)
                        │
                       END
```

`generate_sbom` is named after its output, not the tool. Swapping Trivy for another SBOM generator only changes this node.

---

## State Schema Changes

### `DiscoveryState`

**Removed fields:**
- `package_json_content`, `lock_file_content`, `lock_file_name` — no longer read; Trivy handles all manifest parsing
- `parsed_manifests` — entire manual parsing pipeline gone
- `direct_dependencies`, `transitive_dependencies`, `dependency_tree` — replaced by `sbom_cyclonedx`

**Added fields:**
- `sbom_cyclonedx: dict` — raw CycloneDX output; the canonical dependency representation
- `sbom_result_id: str | None` — MongoDB `_id` of the persisted SBOM document
- `sbom_error: str | None` — set on Trivy failure; short-circuits to `build_dependency_summary`

**Unchanged fields:**
- `repo_url`, `concern`
- `repo_path`
- `project_metadata`, `manifest_files`
- `discovery_summary`, `discovery_error`

### `MainState`

- Removes `direct_dependencies`, `transitive_dependencies`, `dependency_tree`, `parsed_manifests`
- Adds `sbom_cyclonedx: dict`

### `AnalysisState` (`ingestion_subgraphs/_base.py`)

- Removes `direct_dependencies`, `transitive_dependencies`
- Adds `sbom_cyclonedx: dict`

All ingestion subgraphs that previously read dep lists now read from `sbom_cyclonedx`.

---

## Node Changes

### `fetch_repository.py`

**Simplified to clone only.** No file reading. Outputs `repo_path` or `discovery_error`.

### `generate_sbom.py` (new — replaces `parse_package_files.py`)

Runs a single Trivy command:

```
trivy fs --format cyclonedx /repo
```

Saves the CycloneDX document to MongoDB via a `SbomDAO` (moved from `sbom_gen/dao.py` to `discovery/dao.py`). Returns `sbom_cyclonedx`, `sbom_result_id` (the MongoDB `_id`), and `manifest_files` (from `Results[].Target` in the output), or `sbom_error` on failure.

Does **not** run vuln or license scans — those belong to other ingestion subgraphs.  
Does **not** delete `repo_path` — cleanup is the job runner's responsibility.

### `build_dependency_summary.py`

**Rewritten** to consume `sbom_cyclonedx`:

- `project_metadata.name` — from CycloneDX `metadata.component.name`
- `project_metadata.package_manager` — from CycloneDX `metadata.component.type` or lock file hint in `manifest_files`
- `project_metadata.direct_dependencies_count` — from root component's `dependsOn` count in CycloneDX `dependencies`
- LLM summary prompt uses total component count, direct dep count, and component names

On `sbom_error` or `discovery_error`: returns empty `project_metadata` and a failure summary.

### `parse_package_files.py`

**Deleted.**

---

## Deleted: `sbom_gen` Ingestion Subgraph

The entire `src/main_graph/subgraphs/ingestion_subgraphs/sbom_gen/` directory is removed:
- `graph.py`, `state.py`, `constants.py`, `nodes/analyze.py`

`models.py` and `dao.py` **move** to `subgraphs/discovery/`:
- `discovery/models.py` — `SbomEntry` (renamed from `SbomGenEntry`; drops `vulnerabilities`/`licenses` fields since those are not produced here)
- `discovery/dao.py` — `SbomDAO` (same logic, same `sbom_gens` collection)

The `sbom_gens` MongoDB collection continues to be written to, now from the discovery subgraph.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Clone fails | `discovery_error` set; short-circuits to summary |
| Trivy fails / times out | `sbom_error` set; short-circuits to summary with empty `project_metadata` |
| Trivy returns empty SBOM | Zero components; summary reports no dependencies found |

---

## Files Affected

| Action | Path |
|---|---|
| Simplified | `subgraphs/discovery/nodes/fetch_repository.py` |
| New | `subgraphs/discovery/nodes/generate_sbom.py` |
| Rewritten | `subgraphs/discovery/nodes/build_dependency_summary.py` |
| Deleted | `subgraphs/discovery/nodes/parse_package_files.py` |
| New | `subgraphs/discovery/models.py` (moved + simplified from `sbom_gen/models.py`) |
| New | `subgraphs/discovery/dao.py` (moved from `sbom_gen/dao.py`) |
| Updated | `subgraphs/discovery/state.py` |
| Updated | `subgraphs/discovery/graph.py` |
| Updated | `subgraphs/discovery/constants.py` |
| Updated | `main_graph/state.py` |
| Updated | `ingestion_subgraphs/_base.py` |
| Deleted | `ingestion_subgraphs/sbom_gen/` (entire directory) |
