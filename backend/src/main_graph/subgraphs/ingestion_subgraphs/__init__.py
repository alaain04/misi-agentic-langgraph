from src.main_graph.subgraphs.ingestion_subgraphs import registry, repo, runtime
from src.main_graph.subgraphs.ingestion_subgraphs.registry.dao import registry_dao
from src.main_graph.subgraphs.ingestion_subgraphs.repo.dao import repo_dao
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.dao import runtime_dao

_MODULES = [registry, repo, runtime]

SUBGRAPH_REGISTRY = {mod.GRAPH_NAME: mod.subgraph for mod in _MODULES}
SUBGRAPH_DESCRIPTIONS = [mod.describe() for mod in _MODULES]
SUBGRAPH_DEPENDENCIES: dict[str, list[str]] = {
    mod.GRAPH_NAME: mod.DEPENDS_ON for mod in _MODULES
}
SUBGRAPH_DAOS = {
    "registry": registry_dao,
    "repo": repo_dao,
    "runtime": runtime_dao,
}

__all__ = [
    "SUBGRAPH_REGISTRY",
    "SUBGRAPH_DESCRIPTIONS",
    "SUBGRAPH_DEPENDENCIES",
    "SUBGRAPH_DAOS",
]
