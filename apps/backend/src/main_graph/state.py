# backend/src/main_graph/state.py
import operator
from typing import Annotated, Any, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from src.main_graph.subgraphs.discovery.state import ProjectMetadata
from src.models.evidence import Evidence
from src.models.investigation_plan import InvestigationPlan
from src.models.risk_finding import ContradictionReport, RiskFinding


class MainState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────────────────────
    repo_url: str
    concern: str
    job_id: str

    # ── Discovery (unchanged) ────────────────────────────────────────────────
    repo_path: NotRequired[str]
    project_metadata: NotRequired[ProjectMetadata]
    manifest_files: NotRequired[list[str]]
    discovery_summary: NotRequired[str]
    discovery_error: NotRequired[str | None]
    sbom_cyclonedx: NotRequired[dict[str, Any]]
    sbom_result_id: NotRequired[str]
    sbom_error: NotRequired[str | None]

    # ── Investigation plan ───────────────────────────────────────────────────
    investigation_plan: NotRequired[InvestigationPlan]
    messages: Annotated[list, add_messages]

    # ── Evidence (fan-in reducer) ────────────────────────────────────────────
    evidence: Annotated[list[Evidence], operator.add]

    # ── Skill execution (Send() fields) ─────────────────────────────────────
    current_skill_id: NotRequired[str]
    current_dep_name: NotRequired[str]
    current_hypothesis_id: NotRequired[str]

    # ── Correlation outputs ──────────────────────────────────────────────────
    risk_findings: NotRequired[list[RiskFinding]]
    contradictions: NotRequired[list[ContradictionReport]]
    reviewer_feedback: NotRequired[str]
    review_approved: NotRequired[bool]
    review_iterations: NotRequired[int]
    analysis_report: NotRequired[dict[str, Any]]

    # ── Control ──────────────────────────────────────────────────────────────
    cancelled: NotRequired[bool]
