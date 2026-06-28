from __future__ import annotations

import logging

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence

logger = logging.getLogger(__name__)


async def _fetch_registry_metadata(dep_name: str, mcp_client) -> dict:
    try:
        return await mcp_client.call_tool("get_registry_metadata", {"package": dep_name}) or {}
    except Exception:
        logger.warning("SupplyChainSkill: MCP fetch failed for %s", dep_name)
        return {}


def _assess_supply_chain(meta: dict) -> tuple[bool, str, str]:
    flags = []
    if meta.get("has_install_scripts"):
        flags.append("install scripts present")
    if meta.get("owner_changed_recently"):
        flags.append("recent ownership change")
    if meta.get("name_similarity_score", 1.0) < 0.8:
        flags.append("name similarity to popular package (possible typosquat)")
    if flags:
        return True, f"Supply chain risk indicators: {'; '.join(flags)}", "high"
    return False, "No supply chain anomalies detected", "info"


class SupplyChainSkill(InvestigationSkill):
    id = "SupplyChainSkill"
    name = "Supply Chain Integrity Assessment"
    description = "Checks provenance, install scripts, typosquatting indicators"
    trigger_conditions = ["supply chain", "provenance", "typosquat", "install script", "compromise"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["supply_chain_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        mcp_client = ctx.services.get("mcp_client")
        meta = await _fetch_registry_metadata(ctx.dep_name, mcp_client)
        is_risky, signal, severity = _assess_supply_chain(meta)

        return [Evidence(
            kind="supply_chain_signal",
            dep_name=ctx.dep_name,
            skill_id=self.id,
            hypothesis_id=ctx.hypothesis_id,
            signal=signal,
            raw_data=meta,
            source="npm_registry",
            reliability=0.8,
            confidence=0.75 if meta else 0.2,
            severity=severity,
            supports_hypothesis=is_risky,
        )]
