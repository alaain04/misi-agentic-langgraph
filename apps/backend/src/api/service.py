from src.api.schemas import GraphEdgeInfo, GraphInfo, GraphNodeInfo
from src.main_graph.constants import (
    DISCOVERY,
    EVIDENCE_COLLECTOR,
    EVIDENCE_CORRELATOR,
    FINDING_REVIEWER,
    INVESTIGATION_PLANNER,
    REPORT_BUILDER,
    SKILL_DISPATCHER,
    SKILL_EXECUTOR,
)
from src.models.job import Job

_BACKBONE_NODES = [
    (DISCOVERY, 1),
    (INVESTIGATION_PLANNER, 2),
    (SKILL_DISPATCHER, 3),
    (SKILL_EXECUTOR, 4),
    (EVIDENCE_COLLECTOR, 5),
    (EVIDENCE_CORRELATOR, 6),
    (FINDING_REVIEWER, 7),
    (REPORT_BUILDER, 8),
]


def build_graph_info(job: Job) -> GraphInfo:
    nodes: list[GraphNodeInfo] = [
        GraphNodeInfo(id="START", type="terminal", order=0),
        *[GraphNodeInfo(id=name, type="backbone", order=order) for name, order in _BACKBONE_NODES],
        GraphNodeInfo(id="END", type="terminal", order=9),
    ]

    edges: list[GraphEdgeInfo] = [
        GraphEdgeInfo(source="START", target=DISCOVERY),
        GraphEdgeInfo(source=DISCOVERY, target=INVESTIGATION_PLANNER),
        GraphEdgeInfo(source=INVESTIGATION_PLANNER, target=SKILL_DISPATCHER),
        GraphEdgeInfo(source=SKILL_DISPATCHER, target=SKILL_EXECUTOR),
        GraphEdgeInfo(source=SKILL_EXECUTOR, target=EVIDENCE_COLLECTOR),
        GraphEdgeInfo(source=EVIDENCE_COLLECTOR, target=EVIDENCE_CORRELATOR),
        GraphEdgeInfo(source=EVIDENCE_CORRELATOR, target=FINDING_REVIEWER),
        GraphEdgeInfo(source=FINDING_REVIEWER, target=REPORT_BUILDER),
        GraphEdgeInfo(source=FINDING_REVIEWER, target=EVIDENCE_CORRELATOR),
        GraphEdgeInfo(source=REPORT_BUILDER, target="END"),
    ]

    return GraphInfo(nodes=nodes, edges=edges)
