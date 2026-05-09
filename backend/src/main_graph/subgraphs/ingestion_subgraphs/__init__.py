from src.main_graph.subgraphs.ingestion_subgraphs import (
    license_compliance,
    sbom_gen,
    supply_chain,
    vulnerabilities,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.dao import (
    license_compliance_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.sbom_gen.dao import sbom_gen_dao
from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.dao import (
    supply_chain_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.dao import (
    vulnerabilities_dao,
)

_MODULES = [sbom_gen, vulnerabilities, license_compliance, supply_chain]

SUBGRAPH_REGISTRY = {mod.GRAPH_NAME: mod.subgraph for mod in _MODULES}
SUBGRAPH_DESCRIPTIONS = [mod.describe() for mod in _MODULES]
SUBGRAPH_DEPENDENCIES: dict[str, list[str]] = {
    mod.GRAPH_NAME: mod.DEPENDS_ON for mod in _MODULES
}
SUBGRAPH_DAOS = {
    "sbom_gen": sbom_gen_dao,
    "vulnerabilities": vulnerabilities_dao,
    "license_compliance": license_compliance_dao,
    "supply_chain": supply_chain_dao,
}

__all__ = [
    "SUBGRAPH_REGISTRY",
    "SUBGRAPH_DESCRIPTIONS",
    "SUBGRAPH_DEPENDENCIES",
    "SUBGRAPH_DAOS",
]
