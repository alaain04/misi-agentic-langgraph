// src/components/graph/graphDefinition.ts
import type { GraphInfo } from '../../api/types'

export type NodeId =
  | 'START'
  | 'discovery'
  | 'investigation_planner'
  | 'skill_dispatcher'
  | 'skill_executor'
  | 'evidence_collector'
  | 'evidence_correlator'
  | 'finding_reviewer'
  | 'report_builder'
  | 'END'

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

// Static fallback backbone (used when API graph is unavailable)
export const GRAPH_NODES: GraphNodeDef[] = [
  { id: 'START',                label: 'START',                 layer: 0, isSubgraph: false },
  { id: 'discovery',            label: 'discovery',             layer: 1, isSubgraph: false },
  { id: 'investigation_planner',label: 'investigation_planner', layer: 2, isSubgraph: false },
  { id: 'skill_dispatcher',     label: 'skill_dispatcher',      layer: 3, isSubgraph: false },
  { id: 'skill_executor',       label: 'skill_executor',        layer: 4, isSubgraph: false },
  { id: 'evidence_collector',   label: 'evidence_collector',    layer: 5, isSubgraph: false },
  { id: 'evidence_correlator',  label: 'evidence_correlator',   layer: 6, isSubgraph: false },
  { id: 'finding_reviewer',     label: 'finding_reviewer',      layer: 7, isSubgraph: false },
  { id: 'report_builder',       label: 'report_builder',        layer: 8, isSubgraph: false },
  { id: 'END',                  label: 'END',                   layer: 9, isSubgraph: false },
]

export const GRAPH_EDGES: GraphEdgeDef[] = [
  { source: 'START',                target: 'discovery' },
  { source: 'discovery',            target: 'investigation_planner' },
  { source: 'investigation_planner',target: 'skill_dispatcher' },
  { source: 'skill_dispatcher',     target: 'skill_executor' },
  { source: 'skill_executor',       target: 'evidence_collector' },
  { source: 'evidence_collector',   target: 'evidence_correlator' },
  { source: 'evidence_correlator',  target: 'finding_reviewer' },
  { source: 'finding_reviewer',     target: 'evidence_correlator' },
  { source: 'finding_reviewer',     target: 'report_builder' },
  { source: 'report_builder',       target: 'END' },
]

export function buildGraphDef(graph: GraphInfo): { nodes: GraphNodeDef[]; edges: GraphEdgeDef[] } {
  const layerCounter = new Map<number, number>()
  const nodes: GraphNodeDef[] = graph.nodes.map((n) => {
    const layer = n.order
    let laneIndex: number | undefined
    if (n.type === 'subgraph') {
      const current = layerCounter.get(layer) ?? 0
      laneIndex = current
      layerCounter.set(layer, current + 1)
    }
    return {
      id: n.id as NodeId,
      label: n.id,
      layer,
      isSubgraph: n.type === 'subgraph',
      laneIndex,
    }
  })
  const edges: GraphEdgeDef[] = graph.edges.map((e) => ({
    source: e.source as NodeId,
    target: e.target as NodeId,
  }))
  return { nodes, edges }
}
