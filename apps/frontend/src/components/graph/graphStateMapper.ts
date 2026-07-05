// src/components/graph/graphStateMapper.ts
import type {
  StatusResponse,
  Artifact,
  DiscoveryArtifact,
  PlannerArtifact,
  CollectorArtifact,
  ReviewerArtifact,
  ReportArtifact,
} from '../../api/types'
import { GRAPH_NODES, GRAPH_EDGES, buildGraphDef } from './graphDefinition'
import type { GraphRenderData, GraphNodeState, NodeStatus, NodeId } from './graphDefinition'

export function mapResponseToGraphState(response: StatusResponse | null): GraphRenderData {
  if (!response) {
    const nodes = GRAPH_NODES.map(
      (def): GraphNodeState => ({ id: def.id, def, status: 'idle', hasDetail: false }),
    )
    return { nodes, edges: filterEdges(nodes, GRAPH_EDGES) }
  }

  const artifacts = response.artifacts ?? []
  const { nodes: nodeDefs, edges } = response.graph
    ? buildGraphDef(response.graph)
    : { nodes: GRAPH_NODES, edges: GRAPH_EDGES }

  const nodes = nodeDefs.map(
    (def): GraphNodeState => ({
      id: def.id,
      def,
      status: deriveStatus(def.id, response.status, artifacts),
      hasDetail: hasDetail(def.id, artifacts),
    }),
  )

  return { nodes, edges: filterEdges(nodes, edges) }
}

function deriveStatus(id: NodeId, jobStatus: StatusResponse['status'], artifacts: Artifact[]): NodeStatus {
  if (id === 'START') return jobStatus === 'pending' ? 'idle' : 'done'
  if (id === 'END') {
    if (jobStatus === 'done') return 'done'
    if (jobStatus === 'failed') return 'failed'
    return 'idle'
  }

  const artifact = artifacts.find((a) => a.node === id)
  if (artifact) {
    if (artifact.status === 'done') return 'done'
    if (artifact.status === 'failed') return 'failed'
    if (artifact.status === 'cancelled') return 'cancelled'
    if (artifact.status === 'running') {
      if (
        (id === 'investigation_planner' || id === 'finding_reviewer') &&
        'messages' in artifact &&
        (artifact as PlannerArtifact | ReviewerArtifact).messages.length > 0
      ) {
        return 'awaiting'
      }
      return 'active'
    }
  }

  if (jobStatus === 'cancelled') return 'cancelled'
  return 'idle'
}

function hasDetail(id: NodeId, artifacts: Artifact[]): boolean {
  const artifact = artifacts.find((a) => a.node === id)
  if (!artifact) return false
  switch (id) {
    case 'discovery':
      return (artifact as DiscoveryArtifact).steps?.length > 0
    case 'investigation_planner':
      return (artifact as PlannerArtifact).messages.length > 0
    case 'skill_executor': {
      const collector = artifacts.find((a) => a.node === 'evidence_collector') as CollectorArtifact | undefined
      return (collector?.steps?.length ?? 0) > 0
    }
    case 'evidence_correlator':
      return !!(artifact as { data?: unknown }).data
    case 'finding_reviewer':
      return ((artifact as ReviewerArtifact).data?.risk_findings?.length ?? 0) > 0
    case 'report_builder':
      return !!(artifact as ReportArtifact).output
    default:
      return false
  }
}

function filterEdges(
  nodes: GraphNodeState[],
  edges: { source: NodeId; target: NodeId }[],
) {
  const ids = new Set(nodes.map((n) => n.id))
  return edges.filter((e) => ids.has(e.source) && ids.has(e.target))
}
