import type { ComponentType } from 'react'
import type { NodeId } from './graphDefinition'
import type { PanelProps } from './panels/types'
import { DiscoveryPanel } from './panels/DiscoveryPanel'
import { PlannerPanel } from './panels/PlannerPanel'
import { SkillExecutorPanel } from './panels/SkillExecutorPanel'
import { CorrelatorPanel } from './panels/CorrelatorPanel'
import { FindingReviewerPanel } from './panels/FindingReviewerPanel'
import { ReportBuilderPanel } from './panels/ReportBuilderPanel'

export type { PanelProps }
type PanelComponent = ComponentType<PanelProps>

export const NODE_PANEL_REGISTRY = new Map<NodeId, PanelComponent>([
  ['discovery',             DiscoveryPanel],
  ['investigation_planner', PlannerPanel],
  ['skill_executor',        SkillExecutorPanel],
  ['evidence_correlator',   CorrelatorPanel],
  ['finding_reviewer',      FindingReviewerPanel],
  ['report_builder',        ReportBuilderPanel],
])

export function getPanelComponent(id: NodeId): PanelComponent | undefined {
  return NODE_PANEL_REGISTRY.get(id)
}
