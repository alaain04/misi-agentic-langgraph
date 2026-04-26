// ── Node & Edge Types ─────────────────────────────────────────────────────────

import type { GraphInfo } from '../../api/types'

export type NodeId =
  | 'START'
  | 'project_discovery'
  | 'orchestrator'
  | 'registry'
  | 'repo'
  | 'runtime'
  | 'risk_score'
  | 'recommendation'
  | 'summarizer'
  | 'reviewer'
  | 'recommender'
  | 'END'

export type NodeStatus = 'idle' | 'active' | 'done' | 'failed'

export interface GraphNodeDef {
  id: NodeId
  label: string
  /** Horizontal layer (0 = leftmost). Determines x position in layout. */
  layer: number
  /** True for nodes that only appear when included in `plan`. */
  isSubgraph: boolean
  /** Vertical lane within layer 3 (subgraph column). 0 = topmost. */
  laneIndex?: number
}

export interface GraphEdgeDef {
  source: NodeId
  target: NodeId
}

export interface GraphNodeState {
  id: NodeId
  def: GraphNodeDef
  status: NodeStatus
  /** True when the corresponding result data is available for this node. */
  hasDetail: boolean
}

export interface GraphRenderData {
  nodes: GraphNodeState[]
  edges: GraphEdgeDef[]
}

// ── Static Topology ───────────────────────────────────────────────────────────
// To add a new subgraph:
//   1. Add one entry to GRAPH_NODES with isSubgraph: true and next laneIndex
//   2. Add edges: orchestrator → new_node and new_node → final_report

export const GRAPH_NODES: GraphNodeDef[] = [
  { id: 'START', label: 'START', layer: 0, isSubgraph: false },
  { id: 'project_discovery', label: 'discovery', layer: 1, isSubgraph: false },
  { id: 'orchestrator', label: 'orchestrator', layer: 2, isSubgraph: false },
  { id: 'registry', label: 'registry', layer: 3, isSubgraph: true, laneIndex: 0 },
  { id: 'repo', label: 'repo', layer: 3, isSubgraph: true, laneIndex: 1 },
  { id: 'runtime', label: 'runtime', layer: 3, isSubgraph: true, laneIndex: 2 },
  { id: 'risk_score', label: 'risk_score', layer: 3, isSubgraph: true, laneIndex: 3 },
  { id: 'recommendation', label: 'recommendation', layer: 3, isSubgraph: true, laneIndex: 4 },
  { id: 'summarizer', label: 'summarizer', layer: 4, isSubgraph: false },
  { id: 'reviewer', label: 'reviewer', layer: 5, isSubgraph: false },
  { id: 'recommender', label: 'recommender', layer: 6, isSubgraph: false },
  { id: 'END', label: 'END', layer: 7, isSubgraph: false },
]

export const GRAPH_EDGES: GraphEdgeDef[] = [
  { source: 'START', target: 'project_discovery' },
  { source: 'project_discovery', target: 'orchestrator' },
  { source: 'orchestrator', target: 'registry' },
  { source: 'orchestrator', target: 'repo' },
  { source: 'orchestrator', target: 'runtime' },
  { source: 'orchestrator', target: 'risk_score' },
  { source: 'orchestrator', target: 'recommendation' },
  { source: 'registry', target: 'summarizer' },
  { source: 'repo', target: 'summarizer' },
  { source: 'runtime', target: 'summarizer' },
  { source: 'risk_score', target: 'summarizer' },
  { source: 'recommendation', target: 'summarizer' },
  { source: 'summarizer', target: 'reviewer' },
  { source: 'reviewer', target: 'recommender' },
  { source: 'recommender', target: 'END' },
]

// ── Dynamic graph builder ──────────────────────────────────────────────────────

// Human-readable overrides for node IDs whose label should differ from the id
const NODE_LABEL_OVERRIDE: Partial<Record<NodeId, string>> = {
  project_discovery: 'discovery',
}

export function buildGraphDef(graph: GraphInfo): { nodes: GraphNodeDef[]; edges: GraphEdgeDef[] } {
  let subIdx = 0
  const nodes: GraphNodeDef[] = graph.nodes.map((n) => ({
    id: n.id as NodeId,
    label: NODE_LABEL_OVERRIDE[n.id as NodeId] ?? n.id,
    layer: n.order,
    isSubgraph: n.type === 'subgraph',
    laneIndex: n.type === 'subgraph' ? subIdx++ : undefined,
  }))
  const edges: GraphEdgeDef[] = graph.edges.map((e) => ({
    source: e.source as NodeId,
    target: e.target as NodeId,
  }))
  return { nodes, edges }
}
