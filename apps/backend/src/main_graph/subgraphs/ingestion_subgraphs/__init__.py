from src.main_graph.subgraphs.ingestion_subgraphs import (
    impact,
    license_compliance,
    registry,
    repo,
    runtime,
    vulnerabilities,
)
from src.main_graph.subgraphs.ingestion_subgraphs.impact.dao import impact_dao
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.dao import (
    license_compliance_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.registry.dao import registry_dao
from src.main_graph.subgraphs.ingestion_subgraphs.repo.dao import repo_dao
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.dao import runtime_dao
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.dao import (
    vulnerabilities_dao,
)

_MODULES = [vulnerabilities, license_compliance, registry, repo, runtime, impact]

SUBGRAPH_REGISTRY = {mod.GRAPH_NAME: mod.subgraph for mod in _MODULES}
SUBGRAPH_DESCRIPTIONS = [mod.describe() for mod in _MODULES]
SUBGRAPH_DEPENDENCIES: dict[str, list[str]] = {
    mod.GRAPH_NAME: mod.DEPENDS_ON for mod in _MODULES
}
SUBGRAPH_DAOS = {
    "vulnerabilities": vulnerabilities_dao,
    "license_compliance": license_compliance_dao,
    "registry": registry_dao,
    "repo": repo_dao,
    "runtime": runtime_dao,
    "impact": impact_dao,
}

__all__ = [
    "SUBGRAPH_REGISTRY",
    "SUBGRAPH_DESCRIPTIONS",
    "SUBGRAPH_DEPENDENCIES",
    "SUBGRAPH_DAOS",
]
