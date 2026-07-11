// src/components/graph/graphStateMapper.ts
import type {
  StatusResponse,
  Artifact,
  ConductorArtifact,
  ConductorIteration,
  ToolRunnerArtifact,
  HitlGateArtifact,
  ReportArtifact,
} from '../../api/types'
import type { GraphRenderData, GraphNodeState, GraphEdgeDef, NodeStatus } from './graphDefinition'

export function mapResponseToGraphState(response: StatusResponse | null): GraphRenderData {
  if (!response) {
    return {
      nodes: [{
        id: 'START',
        def: { id: 'START', label: 'START', layer: 0, isSubgraph: false },
        status: 'idle',
        hasDetail: false,
      }],
      edges: [],
    }
  }
  return buildGraphFromArtifacts(response.artifacts ?? [], response.status)
}

function buildGraphFromArtifacts(artifacts: Artifact[], jobStatus: StatusResponse['status']): GraphRenderData {
  const conductorArt   = artifacts.find(a => a.node === 'conductor')     as ConductorArtifact | undefined
  const toolRunnerArt  = artifacts.find(a => a.node === 'tool_runner')   as ToolRunnerArtifact | undefined
  const prepArt        = artifacts.find(a => a.node === 'prep')
  const hitlArt        = artifacts.find(a => a.node === 'hitl_gate')     as HitlGateArtifact | undefined
  const reportArt      = artifacts.find(a => a.node === 'report_builder') as ReportArtifact | undefined

  const allIterations  = [...(conductorArt?.iterations ?? [])].sort((a, b) => a.iteration - b.iteration)
  const toolIterations = toolRunnerArt?.iterations ?? []

  // Split conductor iterations around hitl_gate using timestamps
  let preHitl  = allIterations
  let postHitl: ConductorIteration[] = []
  if (hitlArt) {
    const hitlStart = hitlArt.started_at
    preHitl  = allIterations.filter(it => it.started_at < hitlStart)
    postHitl = allIterations.filter(it => it.started_at >= hitlStart)
  }

  const nodes: GraphNodeState[] = []
  const edges: GraphEdgeDef[]   = []
  let layer = 0

  // START
  nodes.push(makeNode('START', 'START', layer++, jobStatus === 'pending' ? 'idle' : 'done'))

  // prep
  nodes.push(makeNode('prep', 'prep', layer++, simpleArtifactStatus(prepArt, jobStatus)))
  edges.push({ source: 'START', target: 'prep' })

  let prevOutputIds: string[] = ['prep']

  const appendIterations = (iterations: ConductorIteration[]) => {
    for (let i = 0; i < iterations.length; i++) {
      const iter = iterations[i]
      const conductorId = `conductor:${iter.iteration}`
      const isLastOverall = iter.iteration === allIterations[allIterations.length - 1]?.iteration
      const conductorStatus: NodeStatus =
        isLastOverall && (jobStatus === 'running' || jobStatus === 'processing') ? 'active' : 'done'

      nodes.push(makeNode(conductorId, `conductor ${iter.iteration}`, layer++, conductorStatus, true))
      prevOutputIds.forEach(id => edges.push({ source: id, target: conductorId }))

      const iterTools = toolIterations.find(t => t.conductor_iteration === iter.iteration)
      if (iterTools && iterTools.tools_run.length > 0) {
        const toolIds: string[] = []
        iterTools.tools_run.forEach((toolName, laneIdx) => {
          const toolId = `tool:${toolName}:${iter.iteration}`
          const hasError = iterTools.errors.some(e => e.tool === toolName)
          nodes.push({
            id: toolId,
            def: { id: toolId, label: toolName, layer, isSubgraph: false, laneIndex: laneIdx },
            status: hasError ? 'failed' : 'done',
            hasDetail: true,
          })
          edges.push({ source: conductorId, target: toolId })
          toolIds.push(toolId)
        })
        layer++
        prevOutputIds = toolIds
      } else {
        prevOutputIds = [conductorId]
      }
    }
  }

  appendIterations(preHitl)

  // hitl_gate
  if (hitlArt) {
    const hitlStatus: NodeStatus =
      hitlArt.status === 'done' ? 'done'
      : hitlArt.messages.length > 0 ? 'awaiting'
      : 'active'
    nodes.push(makeNode('hitl_gate', 'hitl_gate', layer++, hitlStatus, hitlArt.messages.length > 0))
    prevOutputIds.forEach(id => edges.push({ source: id, target: 'hitl_gate' }))
    prevOutputIds = ['hitl_gate']

    if (postHitl.length > 0) {
      appendIterations(postHitl)
    }
  }

  // report_builder
  if (reportArt) {
    const reportStatus = simpleArtifactStatus(reportArt, jobStatus)
    nodes.push(makeNode('report_builder', 'report_builder', layer++, reportStatus, !!(reportArt as ReportArtifact).output))
    prevOutputIds.forEach(id => edges.push({ source: id, target: 'report_builder' }))
    prevOutputIds = ['report_builder']
  }

  // END
  if (jobStatus === 'done' || jobStatus === 'failed' || jobStatus === 'cancelled') {
    const endStatus: NodeStatus =
      jobStatus === 'done' ? 'done' : jobStatus === 'failed' ? 'failed' : 'cancelled'
    nodes.push(makeNode('END', 'END', layer++, endStatus))
    prevOutputIds.forEach(id => edges.push({ source: id, target: 'END' }))
  }

  return { nodes, edges }
}

function makeNode(
  id: string,
  label: string,
  layer: number,
  status: NodeStatus,
  hasDetail = false,
): GraphNodeState {
  return {
    id,
    def: { id, label, layer, isSubgraph: false },
    status,
    hasDetail,
  }
}

function simpleArtifactStatus(artifact: Artifact | undefined, jobStatus: StatusResponse['status']): NodeStatus {
  if (!artifact) return jobStatus === 'cancelled' ? 'cancelled' : 'idle'
  if (artifact.status === 'done')      return 'done'
  if (artifact.status === 'failed')    return 'failed'
  if (artifact.status === 'cancelled') return 'cancelled'
  return 'active'
}
