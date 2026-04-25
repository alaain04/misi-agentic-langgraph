// ── Node & Edge Types ─────────────────────────────────────────────────────────

export type NodeId =
  | 'START'
  | 'project_discovery'
  | 'planner'
  | 'registry'
  | 'repo'
  | 'runtime'
  | 'risk_score'
  | 'recommendation'
  | 'final_report'
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
//   2. Add edges: planner → new_node and new_node → final_report

export const GRAPH_NODES: GraphNodeDef[] = [
  { id: 'START',             label: 'START',            layer: 0, isSubgraph: false },
  { id: 'project_discovery', label: 'project_discovery', layer: 1, isSubgraph: false },
  { id: 'planner',           label: 'planner',           layer: 2, isSubgraph: false },
  { id: 'registry',          label: 'registry',          layer: 3, isSubgraph: true, laneIndex: 0 },
  { id: 'repo',              label: 'repo',              layer: 3, isSubgraph: true, laneIndex: 1 },
  { id: 'runtime',           label: 'runtime',           layer: 3, isSubgraph: true, laneIndex: 2 },
  { id: 'risk_score',        label: 'risk_score',        layer: 3, isSubgraph: true, laneIndex: 3 },
  { id: 'recommendation',    label: 'recommendation',    layer: 3, isSubgraph: true, laneIndex: 4 },
  { id: 'final_report',      label: 'final_report',      layer: 4, isSubgraph: false },
  { id: 'END',               label: 'END',               layer: 5, isSubgraph: false },
]

export const GRAPH_EDGES: GraphEdgeDef[] = [
  { source: 'START',             target: 'project_discovery' },
  { source: 'project_discovery', target: 'planner' },
  { source: 'planner',           target: 'registry' },
  { source: 'planner',           target: 'repo' },
  { source: 'planner',           target: 'runtime' },
  { source: 'planner',           target: 'risk_score' },
  { source: 'planner',           target: 'recommendation' },
  { source: 'registry',          target: 'final_report' },
  { source: 'repo',              target: 'final_report' },
  { source: 'runtime',           target: 'final_report' },
  { source: 'risk_score',        target: 'final_report' },
  { source: 'recommendation',    target: 'final_report' },
  { source: 'final_report',      target: 'END' },
]
