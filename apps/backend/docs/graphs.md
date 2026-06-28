# Graph Architecture

See [architecture.md](architecture.md) for the high-level system overview and request lifecycle.

---

## Main graph

8-node cognitive investigation pipeline.

```mermaid
flowchart TD
    START([start]) --> discovery

    discovery["discovery\n― subgraph ―"]
    discovery --> investigation_planner

    investigation_planner["investigation_planner\n⏸ HITL gate 1\n― LLM ―"]
    investigation_planner -->|plan approved| skill_dispatcher

    skill_dispatcher["skill_dispatcher\n― deterministic ―"]
    skill_dispatcher -->|"Send × N (parallel fan-out)"| skill_executor

    skill_executor["skill_executor\n× N parallel instances\n― per-skill LLM/tool ―"]
    skill_executor -->|fan-in| evidence_collector

    evidence_collector["evidence_collector\n― no-op ―"]
    evidence_collector --> evidence_correlator

    evidence_correlator["evidence_correlator\n― LLM ―"]
    evidence_correlator --> finding_reviewer

    finding_reviewer["finding_reviewer\n⏸ HITL gate 2\n― deterministic + interrupt ―"]
    finding_reviewer -->|"feedback && iterations < 2"| evidence_correlator
    finding_reviewer -->|approved| report_builder

    report_builder["report_builder\n― deterministic ―"]
    report_builder --> END([end])

    classDef hitl fill:#fde68a,stroke:#d97706
    classDef llm fill:#dbeafe,stroke:#2563eb
    classDef det fill:#f3f4f6,stroke:#6b7280
    classDef fanout fill:#ede9fe,stroke:#7c3aed

    class investigation_planner hitl
    class evidence_correlator llm
    class skill_executor fanout
    class skill_dispatcher,evidence_collector,finding_reviewer,report_builder det
```

**HITL gates:**
- `investigation_planner` — graph pauses (`interrupt_before`) before the node, then `interrupt()` inside presents the proposed plan. Resumes on user approve / change / cancel.
- `finding_reviewer` — `interrupt()` fires whenever there are any findings. Auto-approves only when the correlator produces no findings at all.

**Fan-out / fan-in:**
`skill_dispatcher` returns a `list[Send]` — one per `(skill, dep, hypothesis)` assignment. LangGraph executes all `skill_executor` instances in parallel and reduces their `evidence` outputs via `operator.add` before `evidence_collector` runs.

**Re-correlation loop:**
`finding_reviewer` sends feedback back to `evidence_correlator` when quality criteria fail (up to 2 iterations). Criteria: high-severity findings must have ≥ 2 supporting evidence items, risk_score > 7 requires confidence ≥ 0.5, contradictions must be addressed in the summary.

---

## Discovery subgraph

Runs as a single node (`discovery`) inside the main graph.

```mermaid
flowchart TD
    START([start]) --> clone_repository

    clone_repository["clone_repository\n― Docker: alpine/git ―"]
    clone_repository -->|success| inspector_agent
    clone_repository -->|error| build_dependency_summary

    inspector_agent["inspector_agent\n― ReAct LLM agent ―\ntools: list_dir, read_file"]
    inspector_agent -->|lock file present| generate_sbom
    inspector_agent -->|lock_file_missing| lock_generator_agent
    inspector_agent -->|error| build_dependency_summary

    lock_generator_agent["lock_generator_agent\n― ReAct LLM agent ―\ntools: docker_tool, read_file, write_file\nup to 6 install attempts"]
    lock_generator_agent --> generate_sbom

    generate_sbom["generate_sbom\n― Docker: node:XX-alpine ―\nnpm/yarn/pnpm sbom --sbom-format=cyclonedx"]
    generate_sbom --> build_dependency_summary

    build_dependency_summary["build_dependency_summary\n― LLM ―\nproduces discovery_summary"]
    build_dependency_summary --> END([end])

    classDef docker fill:#dcfce7,stroke:#16a34a
    classDef agent fill:#dbeafe,stroke:#2563eb
    classDef llm fill:#ede9fe,stroke:#7c3aed
    classDef det fill:#f3f4f6,stroke:#6b7280

    class clone_repository,generate_sbom docker
    class inspector_agent,lock_generator_agent agent
    class build_dependency_summary llm
```

**Output written to `MainState`:** `repo_path`, `project_metadata`, `manifest_files`, `detected_package_manager`, `docker_image`, `sbom_cyclonedx`, `sbom_result_id`, `discovery_summary`, `discovery_error`.
