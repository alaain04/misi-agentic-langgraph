import type { ComponentType } from 'react'
import type { PanelProps } from './panels/types'
import { nodeKind } from './graphDefinition'
import { ConductorPanel } from './panels/ConductorPanel'
import { ToolPanel } from './panels/ToolPanel'
import { HitlGatePanel } from './panels/HitlGatePanel'
import { DiscoveryPanel } from './panels/DiscoveryPanel'
import { PlannerPanel } from './panels/PlannerPanel'
import { SkillExecutorPanel } from './panels/SkillExecutorPanel'
import { CorrelatorPanel } from './panels/CorrelatorPanel'
import { FindingReviewerPanel } from './panels/FindingReviewerPanel'
import { ReportBuilderPanel } from './panels/ReportBuilderPanel'

export type { PanelProps }

type PanelComponent = ComponentType<PanelProps>

export function getPanelComponent(id: string): PanelComponent | undefined {
  const k = nodeKind(id).kind
  if (k === 'conductor')       return ConductorPanel
  if (k === 'tool')            return ToolPanel
  if (id === 'hitl_gate')      return HitlGatePanel
  if (id === 'report_builder') return ReportBuilderPanel
  if (id === 'discovery')      return DiscoveryPanel
  if (id === 'investigation_planner') return PlannerPanel
  if (id === 'skill_executor') return SkillExecutorPanel
  if (id === 'evidence_correlator')   return CorrelatorPanel
  if (id === 'finding_reviewer')      return FindingReviewerPanel
  return undefined
}
