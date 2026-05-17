from src.api.schemas import GraphEdgeInfo, GraphInfo, GraphNodeInfo
from src.main_graph.constants import (
    CROSS_ANALYZER,
    DISCOVERY,
    ORCHESTRATOR,
    REPORT_REVIEWER,
)
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_REGISTRY
from src.models.job import Job


def build_graph_info(job: Job) -> GraphInfo:
    result = job.result or {}
    plan: list[str] = result.get("plan") or []
    if not plan:
        orch = next(
            (a for a in (job.artifacts or []) if a["node"] == "orchestrator"), None
        )
        if orch:
            proposals = orch.get("proposals") or []
            if proposals:
                plan = proposals[-1].get("plan") or []

    known = set(SUBGRAPH_REGISTRY.keys())
    subgraph_nodes = list(dict.fromkeys(s for s in plan if s in known))

    nodes: list[GraphNodeInfo] = [
        GraphNodeInfo(id="START", type="terminal", order=0),
        GraphNodeInfo(id=DISCOVERY, type="backbone", order=1),
        GraphNodeInfo(id=ORCHESTRATOR, type="backbone", order=2),
        *[GraphNodeInfo(id=s, type="subgraph", order=3) for s in subgraph_nodes],
        GraphNodeInfo(id=CROSS_ANALYZER, type="backbone", order=4),
        GraphNodeInfo(id=REPORT_REVIEWER, type="backbone", order=5),
        GraphNodeInfo(id="END", type="terminal", order=6),
    ]

    edges: list[GraphEdgeInfo] = [
        GraphEdgeInfo(source="START", target=DISCOVERY),
        GraphEdgeInfo(source=DISCOVERY, target=ORCHESTRATOR),
    ]
    if subgraph_nodes:
        for s in subgraph_nodes:
            edges.append(GraphEdgeInfo(source=ORCHESTRATOR, target=s))
            edges.append(GraphEdgeInfo(source=s, target=CROSS_ANALYZER))
    else:
        edges.append(GraphEdgeInfo(source=ORCHESTRATOR, target=CROSS_ANALYZER))
    edges += [
        GraphEdgeInfo(source=CROSS_ANALYZER, target=REPORT_REVIEWER),
        GraphEdgeInfo(source=REPORT_REVIEWER, target="END"),
    ]

    return GraphInfo(nodes=nodes, edges=edges)
