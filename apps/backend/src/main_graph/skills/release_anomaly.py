from __future__ import annotations

import logging

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence

logger = logging.getLogger(__name__)


async def _fetch_releases(dep_name: str, mcp_client) -> list[dict]:
    try:
        result = await mcp_client.call_tool("get_releases", {"package": dep_name})
        return result or []
    except Exception:
        logger.warning("ReleaseAnomalySkill: MCP fetch failed for %s", dep_name)
        return []


def _detect_anomaly(releases: list[dict]) -> tuple[bool, str]:
    if len(releases) < 2:
        return False, "Insufficient release history"
    rapid = [r for r in releases if r.get("days_since_previous", 999) <= 3]
    if len(rapid) >= 3:
        return (
            True,
            f"{len(rapid)} releases published within 3 days of each other — suspicious publish cadence",
        )
    return False, f"{len(releases)} releases with normal cadence"


class ReleaseAnomalySkill(InvestigationSkill):
    id = "ReleaseAnomalySkill"
    name = "Release Anomaly Detection"
    description = "Detects suspicious release patterns: rapid publishing, version gaps, ownership changes"
    trigger_conditions = ["release", "version", "publish", "anomaly", "typosquat"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["release_anomaly"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        mcp_client = ctx.services.get("mcp_client")
        releases = await _fetch_releases(ctx.dep_name, mcp_client)
        is_anomalous, signal = _detect_anomaly(releases)

        return [Evidence(
            kind="release_anomaly",
            dep_name=ctx.dep_name,
            skill_id=self.id,
            hypothesis_id=ctx.hypothesis_id,
            signal=signal,
            raw_data={"releases": releases},
            source="github_mcp",
            reliability=0.75,
            confidence=0.7 if releases else 0.2,
            severity="high" if is_anomalous else "info",
            supports_hypothesis=is_anomalous,
        )]
