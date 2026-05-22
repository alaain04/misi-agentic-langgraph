from __future__ import annotations

import logging

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence

logger = logging.getLogger(__name__)


async def _fetch_ecosystem_data(dep_name: str, mcp_client) -> dict:
    try:
        return await mcp_client.call_tool("get_ecosystem_metrics", {"package": dep_name}) or {}
    except Exception:
        logger.warning("EcosystemSkill: MCP fetch failed for %s", dep_name)
        return {}


class EcosystemSkill(InvestigationSkill):
    id = "EcosystemSkill"
    name = "Ecosystem Reputation Analysis"
    description = "Assesses npm download trends, community health, and package popularity signals"
    trigger_conditions = ["popularity", "downloads", "community", "ecosystem", "reputation"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["ecosystem_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        mcp_client = ctx.services.get("mcp_client")
        data = await _fetch_ecosystem_data(ctx.dep_name, mcp_client)

        downloads = data.get("weekly_downloads", 0)
        dependents = data.get("dependents", 0)
        is_niche = downloads < 1000 and dependents < 10
        signal = (
            f"{ctx.dep_name} is niche: {downloads} weekly downloads, {dependents} dependents"
            if is_niche
            else f"{ctx.dep_name} is well-adopted: {downloads} weekly downloads"
        )

        return [Evidence(
            kind="ecosystem_signal",
            dep_name=ctx.dep_name,
            skill_id=self.id,
            hypothesis_id=ctx.hypothesis_id,
            signal=signal,
            raw_data=data,
            source="npm_registry",
            reliability=0.85,
            confidence=0.7 if data else 0.2,
            severity="medium" if is_niche else "info",
            supports_hypothesis=is_niche,
        )]
