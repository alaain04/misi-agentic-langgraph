import type { ComponentType } from 'react'
import type { NodeId } from './graphDefinition'
import type { PanelProps } from './panels/DiscoveryPanel'
import { DiscoveryPanel } from './panels/DiscoveryPanel'
import { PlannerPanel } from './panels/PlannerPanel'
import { SubgraphPanel } from './panels/SubgraphPanel'
import { FinalReportPanel } from './panels/FinalReportPanel'

// Re-export so callers only need to import from this file
export type { PanelProps }

type PanelComponent = ComponentType<PanelProps>

// Registry: nodeId → panel component
// To add a new subgraph panel: add one line here (or reuse SubgraphPanel).
export const NODE_PANEL_REGISTRY = new Map<NodeId, PanelComponent>([
  ['project_discovery', DiscoveryPanel],
  ['planner', PlannerPanel],
  ['registry', SubgraphPanel],
  ['repo', SubgraphPanel],
  ['runtime', SubgraphPanel],
  ['risk_score', SubgraphPanel],
  ['recommendation', SubgraphPanel],
  ['final_report', FinalReportPanel],
])

export function getPanelComponent(id: NodeId): PanelComponent | undefined {
  return NODE_PANEL_REGISTRY.get(id)
}
