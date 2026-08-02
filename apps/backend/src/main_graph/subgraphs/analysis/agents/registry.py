from __future__ import annotations

from collections.abc import Sequence

from src.main_graph.subgraphs.analysis.agents.base_agent import BaseAgent
from src.main_graph.subgraphs.analysis.agents.license_agent import LicenseAgent
from src.main_graph.subgraphs.analysis.agents.maintenance_agent import MaintenanceAgent
from src.main_graph.subgraphs.analysis.agents.supply_chain_agent import SupplyChainAgent
from src.main_graph.subgraphs.analysis.agents.vulnerability_agent import (
    VulnerabilityAgent,
)
from src.main_graph.subgraphs.analysis.agents.web_research_agent import WebResearchAgent

REGISTRY: dict[str, type[BaseAgent]] = {
    "vulnerability_agent": VulnerabilityAgent,
    "maintenance_agent": MaintenanceAgent,
    "supply_chain_agent": SupplyChainAgent,
    "web_research_agent": WebResearchAgent,
    "license_agent": LicenseAgent,
}


def get_agents() -> dict[str, str]:
    return {k: v for k, v in REGISTRY.items()}


def agents_for_types(types: Sequence[str]) -> list[str]:
    wanted = set(types)
    return [
        agent_type for agent_type, cls in REGISTRY.items() if cls.concern_types & wanted
    ]
