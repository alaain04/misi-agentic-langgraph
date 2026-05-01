import type { StatusResponse, ArtifactInfo } from '../../api/types'
import { GRAPH_NODES, GRAPH_EDGES, buildGraphDef } from './graphDefinition'
import type { GraphRenderData, GraphNodeState, NodeStatus, NodeId } from './graphDefinition'

// Pure function — no side effects, no React.
export function mapResponseToGraphState(response: StatusResponse | null): GraphRenderData {
  // No data yet: show backbone nodes in idle state, no subgraphs
  if (!response) {
    const nodes = GRAPH_NODES.filter((def) => !def.isSubgraph).map(
      (def): GraphNodeState => ({ id: def.id, def, status: 'idle', hasDetail: false }),
    )
    return { nodes, edges: filterEdges(nodes, GRAPH_EDGES) }
  }

  const { status, results } = response
  const artifactMap = buildArtifactMap(response.artifacts ?? [])

  // Use backend-provided graph topology if available; fall back to static backbone-only
  const { nodes: nodeDefs, edges } = response.graph
    ? buildGraphDef(response.graph)
    : {
        nodes: GRAPH_NODES.filter((def) => !def.isSubgraph),
        edges: GRAPH_EDGES,
      }

  const nodes = nodeDefs.map(
    (def): GraphNodeState => ({
      id: def.id,
      def,
      status: deriveStatus(def.id, status, results, artifactMap),
      hasDetail: hasDetail(def.id, results, artifactMap),
    }),
  )

  return { nodes, edges: filterEdges(nodes, edges) }
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
    if (id === 'discovery') return 'done'
    if (id === 'orchestrator') return 'done'
    return 'idle'
  }
  if (jobStatus === 'pending') return 'idle'
  if (jobStatus === 'running') return 'idle' // node hasn't started yet (no artifact)
  if (jobStatus === 'done') return 'done'
  // failed job: nodes without artifacts/results are failed
  if (id === 'discovery') return results?.discovery ? 'done' : 'failed'
  if (id === 'orchestrator') return 'failed'
  if (id === 'summarizer') return results?.summary ? 'done' : 'failed'
  if (id === 'reviewer') return results?.review ? 'done' : 'failed'
  if (id === 'recommender') return results?.recommendation ? 'done' : 'failed'
  const found = results?.subgraph_results?.some((r) => r.subgraph === id)
  return found ? 'done' : 'failed'
}

function hasDetail(
  id: NodeId,
  results: StatusResponse['results'],
  artifactMap: Map<NodeId, ArtifactInfo>,
): boolean {
  if (id === 'discovery') return !!results?.discovery
  if (id === 'orchestrator') return !!artifactMap.get('orchestrator')?.proposals?.length
  if (id === 'summarizer') return !!(artifactMap.get('summarizer')?.output ?? results?.summary)
  if (id === 'reviewer') return !!(artifactMap.get('reviewer')?.output ?? results?.review)
  if (id === 'recommender')
    return !!(artifactMap.get('recommender')?.output ?? results?.recommendation)
  const artifact = artifactMap.get(id)
  return !!(artifact?.result ?? results?.subgraph_results?.some((r) => r.subgraph === id))
}

function filterEdges(nodes: GraphNodeState[], edges: ReturnType<typeof buildGraphDef>['edges']) {
  const ids = new Set(nodes.map((n) => n.id))
  return edges.filter((e) => ids.has(e.source) && ids.has(e.target))
}
