"""Per-role LLM resolution: the one place that decides which Model backs
each AgentRole. Call sites ask for a role, never a literal Model, so a
comparison experiment is a settings/env change, not a source edit — see
docs/model-selection.md section 6.1.
"""

from enum import StrEnum

from langchain_core.language_models import BaseChatModel
from langchain_core.rate_limiters import BaseRateLimiter

from src.utils.config import settings
from src.utils.llm import Model, get_llm


class AgentRole(StrEnum):
    UNDERSTAND_CONCERN = "understand_concern"
    ANALYSIS_ROOT_DEEPAGENT = "analysis_root_deepagent"
    ANALYSIS_DISPATCH = "analysis_dispatch"
    COVERAGE_JUDGE = "coverage_judge"
    SPECIALIST_AGENT = "specialist_agent"
    ANALYSIS_CRITIQUE = "analysis_critique"
    REPORT_SYNTHESIZER = "report_synthesizer"
    FINDING_ENRICHER = "finding_enricher"
    IMPACT_ANALYSIS = "impact_analysis"
    REPORT_CRITIQUE = "report_critique"
    REMEDIATION_CLASSIFY = "remediation_classify"
    REMEDIATION_INVESTIGATE = "remediation_investigate"
    REMEDIATION_PLAN = "remediation_plan"
    REMEDIATION_EXECUTION_DEEPAGENT = "remediation_execution_deepagent"


# One default model, applied everywhere by policy — the "no differentiation
# to justify yet" baseline from docs/model-selection.md section 2. Change
# this only with a decision record (section 8); change a single role via
# settings.model_overrides instead.
_DEFAULT_MODEL = Model.GPT_5_4_MINI


def resolve_model(role: AgentRole) -> Model:
    override = settings.model_overrides.get(role.value)
    if override is None:
        return _DEFAULT_MODEL
    return Model(override)  # raises ValueError loudly on a typo'd override


def get_role_llm(
    role: AgentRole,
    *,
    rate_limiter: BaseRateLimiter | None = None,
    max_retries: int | None = None,
) -> BaseChatModel:
    llm = get_llm(
        resolve_model(role), rate_limiter=rate_limiter, max_retries=max_retries
    )
    return llm.with_config(tags=[f"agent_role:{role.value}"])
