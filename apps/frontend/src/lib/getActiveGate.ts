// src/lib/getActiveGate.ts
import type { Artifact, PlannerArtifact, ReviewerArtifact } from '../api/types'

export type Gate = 'investigation_planner' | 'finding_reviewer'

export function getActiveGate(artifacts: Artifact[]): Gate | null {
  const gates: Gate[] = ['investigation_planner', 'finding_reviewer']
  for (const node of gates) {
    const artifact = artifacts.find((a) => a.node === node) as
      | PlannerArtifact
      | ReviewerArtifact
      | undefined
    if (artifact?.status === 'running' && artifact.messages.length > 0) {
      return node
    }
  }
  return null
}
