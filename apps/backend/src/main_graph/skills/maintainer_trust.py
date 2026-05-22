from __future__ import annotations

import logging

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence

logger = logging.getLogger(__name__)


async def _fetch_repo_data(dep_name: str, mcp_client) -> dict:
    try:
        result = await mcp_client.call_tool("get_repository_activity", {"package": dep_name})
        return result or {}
    except Exception:
        logger.warning("MaintainerTrustSkill: MCP fetch failed for %s", dep_name)
        return {}


def _assess_health(data: dict) -> tuple[bool, str, str]:
    commits = data.get("commits_last_90_days", 0)
    open_issues = data.get("open_issues", 0)
    closed = data.get("closed_issues_last_90_days", 0)
    contributors = data.get("contributors", 1)

    if commits == 0 and open_issues > 50 and closed == 0:
        return (
            True,
            f"No commits in 90 days, {open_issues} unresolved issues, {contributors} contributor(s) — likely abandoned",
            "high",
        )
    if commits < 5 and contributors <= 1:
        return True, f"Low activity: {commits} commits/90d, single maintainer", "medium"
    return False, f"Active: {commits} commits/90d, {contributors} contributors", "info"


class MaintainerTrustSkill(InvestigationSkill):
    id = "MaintainerTrustSkill"
    name = "Maintainer Trust Analysis"
    description = "Evaluates maintainer activity, commit patterns, and issue responsiveness"
    trigger_conditions = ["abandoned", "maintainer", "activity", "bus factor"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["maintainer_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        mcp_client = ctx.services.get("mcp_client")
        data = await _fetch_repo_data(ctx.dep_name, mcp_client)
        is_concerning, signal, severity = _assess_health(data)
        confidence = 0.75 if data else 0.2

        return [Evidence(
            kind="maintainer_signal",
            dep_name=ctx.dep_name,
            skill_id=self.id,
            hypothesis_id=ctx.hypothesis_id,
            signal=signal,
            raw_data=data,
            source="github_mcp",
            reliability=0.8 if data else 0.3,
            confidence=confidence,
            severity=severity,
            supports_hypothesis=is_concerning,
        )]
