// src/lib/getActiveGate.ts
import type { Artifact, HitlGateArtifact, PlannerArtifact, ReviewerArtifact } from '../api/types'

export type Gate = 'hitl_gate' | 'investigation_planner' | 'finding_reviewer'

export function getActiveGate(artifacts: Artifact[]): Gate | null {
  const gates: Gate[] = ['hitl_gate', 'investigation_planner', 'finding_reviewer']
  for (const node of gates) {
    const artifact = artifacts.find((a) => a.node === node) as
      | HitlGateArtifact
      | PlannerArtifact
      | ReviewerArtifact
      | undefined
    if (artifact?.status === 'running' && 'messages' in artifact && artifact.messages.length > 0) {
      return node
    }
  }
  return null
}
