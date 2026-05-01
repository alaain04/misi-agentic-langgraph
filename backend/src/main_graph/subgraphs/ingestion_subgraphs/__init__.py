from src.main_graph.subgraphs.ingestion_subgraphs import registry, repo, runtime

_MODULES = [registry, repo, runtime]

SUBGRAPH_REGISTRY = {mod.GRAPH_NAME: mod.subgraph for mod in _MODULES}
SUBGRAPH_DESCRIPTIONS = [mod.describe() for mod in _MODULES]
SUBGRAPH_DEPENDENCIES: dict[str, list[str]] = {
    mod.GRAPH_NAME: mod.DEPENDS_ON for mod in _MODULES
}

__all__ = ["SUBGRAPH_REGISTRY", "SUBGRAPH_DESCRIPTIONS", "SUBGRAPH_DEPENDENCIES"]
