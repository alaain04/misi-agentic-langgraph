from src.api.schemas import GraphEdgeInfo, GraphInfo, GraphNodeInfo
from src.main_graph.constants import CONDUCTOR, HITL_GATE, PREP, REPORT_BUILDER, TOOL_RUNNER
from src.models.job import Job

_BACKBONE_NODES = [
    (PREP, 1),
    (CONDUCTOR, 2),
    (TOOL_RUNNER, 3),
    (HITL_GATE, 4),
    (REPORT_BUILDER, 5),
]


def build_graph_info(job: Job) -> GraphInfo:
    nodes: list[GraphNodeInfo] = [
        GraphNodeInfo(id="START", type="terminal", order=0),
        *[GraphNodeInfo(id=name, type="backbone", order=order) for name, order in _BACKBONE_NODES],
        GraphNodeInfo(id="END", type="terminal", order=6),
    ]

    edges: list[GraphEdgeInfo] = [
        GraphEdgeInfo(source="START", target=PREP),
        GraphEdgeInfo(source=PREP, target=CONDUCTOR),
        GraphEdgeInfo(source=CONDUCTOR, target=TOOL_RUNNER),
        GraphEdgeInfo(source=TOOL_RUNNER, target=CONDUCTOR),
        GraphEdgeInfo(source=CONDUCTOR, target=HITL_GATE),
        GraphEdgeInfo(source=HITL_GATE, target=CONDUCTOR),
        GraphEdgeInfo(source=CONDUCTOR, target=REPORT_BUILDER),
        GraphEdgeInfo(source=REPORT_BUILDER, target="END"),
    ]

    return GraphInfo(nodes=nodes, edges=edges)
