from src.main_graph.subgraphs.ingestion_subgraphs import (
    registry_subgraph,
    repo_subgraph,
    runtime_subgraph,
)

SUBGRAPH_REGISTRY = {
    "registry": registry_subgraph,
    "repo": repo_subgraph,
    "runtime": runtime_subgraph,
}
