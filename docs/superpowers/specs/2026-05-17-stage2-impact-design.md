# Stage 2 — Impact Analysis Design

**Date:** 2026-05-17
**Status:** Draft — pending brainstorm session
**Parent spec:** [ingestion-subgraphs-design.md](2026-05-17-ingestion-subgraphs-design.md)

---

## Context

Stage 2 runs after `risk_ranker` has identified high-risk dependencies. For each high-risk dep, the `impact` subgraph analyzes how that dependency is actually used inside the **user's cloned project** — not the dependency's own source.

Two questions it answers:
1. **Static usage** — which files import this dep and what part of its API is used?
2. **Blast radius** — how many other packages in the dependency tree would be affected if this dep changed or was removed?

This maps to thesis objective 3: *Analyze the use and impact of each dependency in the project.*

---

## Agentic Design

`impact` is a fully agentic subgraph. An LLM agent drives the analysis using a set of filesystem and SBOM tools, then writes a structured result.

All agentic nodes follow the same pattern: LLM bound to typed tools, ReAct loop, structured output via a `save_*` tool call.

---

## Tools

| Tool | Signature | What it does |
|---|---|---|
| `list_source_files` | `(repo_path: str, extensions: list[str] = [".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"]) -> list[str]` | Recursively lists all source files, excluding `node_modules` |
| `find_usages` | `(dep_name: str, repo_path: str) -> list[dict]` | Greps for `import … from 'dep'`, `require('dep')`, and dynamic `import('dep')`; returns `[{file, line, statement}]` |
| `read_file_excerpt` | `(path: str, around_line: int, context: int = 5) -> str` | Reads ±N lines around a usage site so the LLM can understand which APIs are called |
| `get_direct_dependents` | `(dep_name: str, sbom: dict) -> list[str]` | Returns packages in `sbom.dependencies` that list `dep_name` as a direct dependency |
| `get_blast_radius` | `(dep_name: str, sbom: dict) -> dict` | Traverses the full SBOM dep tree; returns `{direct_dependents: int, transitive_dependents: int, max_depth: int}` |
| `save_impact` | `(entry: dict) -> str` | Persists the impact result to MongoDB; returns `result_id` |

---

## System Prompt Direction

The agent is instructed to:
1. Find all usages of `dependency_name` across the project's source files
2. Read a representative sample of usage sites (up to 10) to characterize how the API is used
3. Compute the blast radius from the SBOM
4. Write a human-readable summary of both usage and blast radius
5. Call `save_impact` with the structured result

---

## Output Schema

```python
class ImpactEntry(BaseModel):
    dep_name: str
    usage_count: int
    affected_files: list[str]
    api_surface_used: list[str]       # e.g. ["useState", "useEffect"]
    usage_summary: str                # LLM narrative
    direct_dependents: int
    transitive_dependents: int
    max_depth: int
    blast_radius_summary: str         # LLM narrative
```

---

## Open Questions for Brainstorm Session

- Should `find_usages` use plain grep or a proper JS/TS AST parser (e.g. tree-sitter)? AST is more accurate but adds a dependency.
- How do we handle aliased imports (`import _ from 'lodash'` vs `import { map } from 'lodash/map'`)?
- Should the agent also flag files where the dep is used as a type-only import (TypeScript)?
- How many usage-site excerpts should the agent read before summarizing (cost vs. accuracy)?
- Should impact run for medium-risk deps too, not just high-risk?
