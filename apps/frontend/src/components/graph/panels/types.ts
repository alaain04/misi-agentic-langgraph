import type { NodeId } from '../graphDefinition'
import type { Artifact, JobResult } from '../../../api/types'

export interface PanelProps {
  nodeId: NodeId
  results: JobResult | null
  artifacts: Artifact[]
}
