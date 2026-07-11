// src/components/graph/graphDefinition.ts

export type NodeId = string

export type NodeStatus = 'idle' | 'active' | 'awaiting' | 'done' | 'failed' | 'cancelled'

export interface GraphNodeDef {
  id: NodeId
  label: string
  layer: number
  isSubgraph: boolean
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
  hasDetail: boolean
}

export interface GraphRenderData {
  nodes: GraphNodeState[]
  edges: GraphEdgeDef[]
}

export type NodeKind =
  | { kind: 'START' }
  | { kind: 'END' }
  | { kind: 'prep' }
  | { kind: 'hitl_gate' }
  | { kind: 'report_builder' }
  | { kind: 'discovery' }
  | { kind: 'conductor'; iter: number }
  | { kind: 'tool'; name: string; iter: number }
  | { kind: 'unknown' }

export function nodeKind(id: string): NodeKind {
  if (id === 'START') return { kind: 'START' }
  if (id === 'END') return { kind: 'END' }
  if (id === 'prep') return { kind: 'prep' }
  if (id === 'hitl_gate') return { kind: 'hitl_gate' }
  if (id === 'report_builder') return { kind: 'report_builder' }
  if (id === 'discovery') return { kind: 'discovery' }
  const conductorMatch = id.match(/^conductor:(\d+)$/)
  if (conductorMatch) return { kind: 'conductor', iter: parseInt(conductorMatch[1], 10) }
  const toolMatch = id.match(/^tool:(.+):(\d+)$/)
  if (toolMatch) return { kind: 'tool', name: toolMatch[1], iter: parseInt(toolMatch[2], 10) }
  return { kind: 'unknown' }
}
