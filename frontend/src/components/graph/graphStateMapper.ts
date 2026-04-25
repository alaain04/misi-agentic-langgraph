import type { StatusResponse, ArtifactInfo } from '../../api/types'
import { GRAPH_NODES, GRAPH_EDGES } from './graphDefinition'
import type { GraphRenderData, GraphNodeState, NodeStatus, NodeId } from './graphDefinition'

// Pure function — no side effects, no React.
export function mapResponseToGraphState(response: StatusResponse | null): GraphRenderData {
  // No data yet: show backbone nodes in idle state, no subgraphs
  if (!response) {
    const nodes = GRAPH_NODES.filter((def) => !def.isSubgraph).map(
      (def): GraphNodeState => ({ id: def.id, def, status: 'idle', hasDetail: false }),
    )
    return { nodes, edges: filterEdges(nodes) }
  }

  const { status, results } = response
  const plan = results?.plan ?? []
  const activeSubgraphIds = new Set<NodeId>(plan as NodeId[])
  const artifactMap = buildArtifactMap(response.artifacts ?? [])

  // During pending/running, subgraph plan is unknown — hide subgraph nodes
  // unless they already appear in artifacts (they started running)
  const showSubgraphs = status === 'done' || status === 'failed' || status === 'awaiting_approval'
  const hasArtifactSubgraphs = (response.artifacts ?? []).some((a) =>
    GRAPH_NODES.find((n) => n.id === a.node && n.isSubgraph),
  )

  const nodes = GRAPH_NODES.filter(
    (def) =>
      !def.isSubgraph ||
      (showSubgraphs && activeSubgraphIds.has(def.id)) ||
      (hasArtifactSubgraphs && artifactMap.has(def.id as NodeId)),
  ).map(
    (def): GraphNodeState => ({
      id: def.id,
      def,
      status: deriveStatus(def.id, status, results, activeSubgraphIds, artifactMap),
      hasDetail: hasDetail(def.id, results),
    }),
  )

  return { nodes, edges: filterEdges(nodes) }
}

function buildArtifactMap(artifacts: ArtifactInfo[]): Map<NodeId, ArtifactInfo> {
  const map = new Map<NodeId, ArtifactInfo>()
  for (const a of artifacts) {
    map.set(a.node as NodeId, a)
  }
  return map
}

function deriveStatus(
  id: NodeId,
  jobStatus: StatusResponse['status'],
  results: StatusResponse['results'],
  _plan: Set<NodeId>,
  artifactMap: Map<NodeId, ArtifactInfo>,
): NodeStatus {
  // Terminal nodes
  if (id === 'START') {
    if (jobStatus === 'pending') return 'idle'
    return 'done'
  }
  if (id === 'END') {
    if (jobStatus === 'done') return 'done'
    if (jobStatus === 'failed') return 'failed'
    return 'idle'
  }

  // Use artifact status if available — most accurate source of truth
  const artifact = artifactMap.get(id)
  if (artifact) {
    if (artifact.status === 'running') return 'active'
    if (artifact.status === 'done') return 'done'
    if (artifact.status === 'failed') return 'failed'
  }

  // Fallback: derive from job-level status
  if (jobStatus === 'awaiting_approval') {
    if (id === 'project_discovery') return 'done'
    if (id === 'planner') return 'done'
    return 'idle'
  }
  if (jobStatus === 'pending') return 'idle'
  if (jobStatus === 'running') return 'idle' // node hasn't started yet (no artifact)
  if (jobStatus === 'done') return 'done'
  // failed job: nodes without results are failed
  if (id === 'project_discovery') return results?.discovery ? 'done' : 'failed'
  if (id === 'planner') return results?.plan ? 'done' : 'failed'
  if (id === 'final_report') return results?.final_report != null ? 'done' : 'failed'
  const found = results?.subgraph_results?.some((r) => r.subgraph === id)
  return found ? 'done' : 'failed'
}

function hasDetail(id: NodeId, results: StatusResponse['results']): boolean {
  if (!results) return false
  if (id === 'project_discovery') return !!results.discovery
  if (id === 'planner') return !!(results.plan && results.plan.length > 0)
  if (id === 'final_report') return !!results.final_report
  return !!results.subgraph_results?.some((r) => r.subgraph === id)
}

function filterEdges(nodes: GraphNodeState[]) {
  const ids = new Set(nodes.map((n) => n.id))
  return GRAPH_EDGES.filter((e) => ids.has(e.source) && ids.has(e.target))
}
