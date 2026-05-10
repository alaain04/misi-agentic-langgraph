from src.main_graph.subgraphs.ingestion_subgraphs import (
    dependency_freshness,
    license_compliance,
    supply_chain,
    vulnerabilities,
)
from src.main_graph.subgraphs.ingestion_subgraphs.dependency_freshness.dao import (
    dependency_freshness_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.dao import (
    license_compliance_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.dao import (
    supply_chain_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.dao import (
    vulnerabilities_dao,
)

_MODULES = [vulnerabilities, license_compliance, supply_chain, dependency_freshness]

SUBGRAPH_REGISTRY = {mod.GRAPH_NAME: mod.subgraph for mod in _MODULES}
SUBGRAPH_DESCRIPTIONS = [mod.describe() for mod in _MODULES]
SUBGRAPH_DEPENDENCIES: dict[str, list[str]] = {
    mod.GRAPH_NAME: mod.DEPENDS_ON for mod in _MODULES
}
SUBGRAPH_DAOS = {
    "vulnerabilities": vulnerabilities_dao,
    "license_compliance": license_compliance_dao,
    "supply_chain": supply_chain_dao,
    "dependency_freshness": dependency_freshness_dao,
}

__all__ = [
    "SUBGRAPH_REGISTRY",
    "SUBGRAPH_DESCRIPTIONS",
    "SUBGRAPH_DEPENDENCIES",
    "SUBGRAPH_DAOS",
]
