import type { ComponentType } from 'react'
import type { NodeId } from './graphDefinition'
import type { PanelProps } from './panels/types'
import { DiscoveryPanel } from './panels/DiscoveryPanel'
import { PlannerPanel } from './panels/PlannerPanel'

// Re-export so callers only need to import from this file
export type { PanelProps }

type PanelComponent = ComponentType<PanelProps>

// Registry: nodeId → panel component
// To add a new node panel: add one line here.
export const NODE_PANEL_REGISTRY = new Map<NodeId, PanelComponent>([
  ['discovery', DiscoveryPanel],
  ['investigation_planner', PlannerPanel],
])

export function getPanelComponent(id: NodeId): PanelComponent | undefined {
  return NODE_PANEL_REGISTRY.get(id)
}
