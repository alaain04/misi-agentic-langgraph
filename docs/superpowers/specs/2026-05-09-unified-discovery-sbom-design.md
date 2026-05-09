# Design: Unified Discovery + SBOM Subgraph

**Date:** 2026-05-09  
**Status:** Approved

---

## Problem

The discovery subgraph and the `sbom_gen` ingestion subgraph both detect dependencies:

- **Discovery** manually parses `package.json` and lock files (npm/yarn/pnpm) to produce `direct_dependencies`, `transitive_dependencies`, and `dependency_tree`.
- **SBOM Gen** runs Trivy via Docker on the same cloned repo to produce a CycloneDX SBOM with all components, plus vulnerability and license data.

This is duplication. Trivy's CycloneDX output is a complete, standardized bill of materials that already contains every component with resolved versions — the manual lock file parsers are a less-accurate reimplementation of what Trivy does natively.

Additionally, SBOM Gen runs as an ingestion subgraph (after the orchestrator plans), but the orchestrator needs dependency data to make its plan. This creates a sequencing mismatch.

---

## Goal

- Make Trivy the single source of truth for dependency data.
- Remove all manual lock file parsers.
- Merge the Trivy scan into discovery so the orchestrator has full SBOM data before planning.
- Delete the `sbom_gen` ingestion subgraph.

---

## Graph Topology

```
START
  └─► fetch_repository          (git clone → repo_path, package.json content)
        │
        ├─► [discovery_error] ──────────────────► build_dependency_summary
        │
        └─► generate_sbom                         (Trivy: CycloneDX + vuln/license JSON)
              │
              ├─► [sbom_error] ────────────────► build_dependency_summary
              │
              └─► build_dependency_summary      (LLM summary from SBOM data)
                        │
                       END
```

`generate_sbom` is named after its output (an SBOM), not the tool. If Trivy is replaced in the future, only this node changes.

---

## State Schema Changes

### `DiscoveryState`

**Removed fields** (manual parsing internals):
- `package_json_content`
- `lock_file_content`
- `parsed_manifests`

**Renamed/repurposed**:
- `lock_file_name` → kept as `NotRequired[str]`; now set by `fetch_repository` via a quick `os.path.exists` check (no file read) for package manager detection. Not passed forward to Trivy.

**Added fields**:
- `direct_dep_names: list[str]` — direct dependency names extracted from `package.json` by `fetch_repository`; consumed by `build_dependency_summary` to split direct vs. transitive
- `sbom_cyclonedx: dict` — raw CycloneDX output from `generate_sbom`
- `vulnerabilities: list[TrivyVulnerability]` — parsed from Trivy JSON scan
- `licenses: list[TrivyLicenseFinding]` — parsed from Trivy JSON scan
- `sbom_error: str | None` — set on Trivy failure; short-circuits to `build_dependency_summary`

**Unchanged fields**:
- `repo_url`, `concern`
- `repo_path`
- `project_metadata`, `direct_dependencies`, `transitive_dependencies`, `dependency_tree`, `manifest_files`
- `discovery_summary`, `discovery_error`

### `MainState`

Gains the same three new fields: `sbom_cyclonedx`, `vulnerabilities`, `licenses`.

### `AnalysisState` (`ingestion_subgraphs/_base.py`)

Gains `vulnerabilities` and `licenses` so downstream ingestion subgraphs (e.g. `license_compliance`) can consume Trivy findings directly from state instead of re-running scans.

---

## Node Changes

### `fetch_repository.py`

**Simplified.** Clones the repo (same as today), reads `package.json` (one `json.loads` call to extract `dependencies` + `devDependencies` key names into `direct_dep_names`), and does a quick `os.path.exists` check for lock files to detect `lock_file_name` (used only for `project_metadata.package_manager`). Lock file contents are not read — Trivy handles all lock file parsing.

Output: `repo_path`, `direct_dep_names`, `lock_file_name`, or `discovery_error`.

### `generate_sbom.py` (new — replaces `parse_package_files.py`)

Runs two Trivy commands in parallel (same implementation as `sbom_gen/nodes/analyze.py` today):
1. `fs --format json --scanners vuln,license` → vulnerability + license data
2. `fs --format cyclonedx` → full SBOM

Parses vulnerabilities and licenses from the JSON scan. Populates `manifest_files` from the `Results[].Target` entries in the Trivy JSON output (the files Trivy actually scanned). Returns `sbom_cyclonedx`, `vulnerabilities`, `licenses`, `manifest_files`, or `sbom_error` on failure.

Deletes the temp dir (`repo_path`) after both Trivy scans complete — same responsibility as `sbom_gen` today. The job runner's finalizer remains a safety-net fallback.

### `build_dependency_summary.py`

**Rewritten** to consume CycloneDX components instead of `parsed_manifests`:

- All CycloneDX `components` → full dependency set with resolved versions
- Cross-reference component names against direct dep names from `package.json` → split into `direct_dependencies` / `transitive_dependencies`
- Dependency tree built from CycloneDX `dependencies` edges (BOM-ref relationships)
- LLM summary prompt updated to include vuln count and license count as additional context

On `sbom_error` (same as existing `discovery_error` path): returns empty dep lists and a failure summary.

### `parse_package_files.py`

**Deleted.**

---

## Deleted: `sbom_gen` Ingestion Subgraph

The entire `src/main_graph/subgraphs/ingestion_subgraphs/sbom_gen/` directory is removed:
- `graph.py`, `state.py`, `constants.py`, `models.py`, `dao.py`
- `nodes/analyze.py`

`TrivyVulnerability` and `TrivyLicenseFinding` models move to `discovery/models.py` (or a shared `models.py`) since they are now produced by discovery and consumed broadly.

The MongoDB `sbom_gens` collection is no longer written to. SBOM data lives in state and is accessible to downstream subgraphs via `upstream_results`.

---

## Direct vs. Transitive Extraction

1. `fetch_repository` reads `package.json` → set of direct dep names.
2. `generate_sbom` runs Trivy → CycloneDX `components` list (all resolved packages).
3. `build_dependency_summary` splits:
   - component name in direct dep names → `DependencyEntry` in `direct_dependencies`
   - otherwise → `DependencyEntry` in `transitive_dependencies`
4. Dependency tree uses CycloneDX `dependencies[].dependsOn` BOM-ref edges.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Clone fails | `discovery_error` set in `fetch_repository`; short-circuits to summary |
| No `package.json` | `discovery_error` set; short-circuits to summary |
| Trivy fails / times out | `sbom_error` set in `generate_sbom`; short-circuits to summary with empty dep lists |
| Trivy returns empty SBOM | Treated as zero components; summary reports no dependencies found |

---

## Files Affected

| Action | Path |
|---|---|
| Simplified | `subgraphs/discovery/nodes/fetch_repository.py` |
| New | `subgraphs/discovery/nodes/generate_sbom.py` |
| Rewritten | `subgraphs/discovery/nodes/build_dependency_summary.py` |
| Deleted | `subgraphs/discovery/nodes/parse_package_files.py` |
| Updated | `subgraphs/discovery/state.py` |
| Updated | `subgraphs/discovery/graph.py` |
| Updated | `subgraphs/discovery/constants.py` |
| New | `subgraphs/discovery/models.py` (TrivyVulnerability, TrivyLicenseFinding) |
| Updated | `main_graph/state.py` (MainState) |
| Updated | `ingestion_subgraphs/_base.py` (AnalysisState) |
| Deleted | `ingestion_subgraphs/sbom_gen/` (entire directory) |
