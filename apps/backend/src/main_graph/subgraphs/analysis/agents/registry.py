from __future__ import annotations

from src.main_graph.subgraphs.analysis.agents.base_agent import BaseAgent
from src.main_graph.subgraphs.analysis.agents.maintenance_agent import MaintenanceAgent
from src.main_graph.subgraphs.analysis.agents.supply_chain_agent import SupplyChainAgent
from src.main_graph.subgraphs.analysis.agents.vulnerability_agent import VulnerabilityAgent
from src.main_graph.subgraphs.analysis.agents.web_research_agent import WebResearchAgent

REGISTRY: dict[str, type[BaseAgent]] = {
    "vulnerability_agent": VulnerabilityAgent,
    "maintenance_agent": MaintenanceAgent,
    "supply_chain_agent": SupplyChainAgent,
    "web_research_agent": WebResearchAgent,
}


def get_agent_descriptions() -> dict[str, str]:
    return {k: v.description for k, v in REGISTRY.items()}
