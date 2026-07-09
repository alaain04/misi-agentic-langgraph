import { describe, it, expect } from 'vitest'
import { mapResponseToGraphState } from './graphStateMapper'
import type { StatusResponse, ConductorArtifact, ToolRunnerArtifact, HitlGateArtifact } from '../../api/types'

function makeResponse(overrides: Partial<StatusResponse>): StatusResponse {
  return {
    trace_id: 'test-id',
    status: 'running',
    metadata: { repo_url: 'https://github.com/x/y', concern: 'security', autopilot: false },
    completed_at: null,
    results: null,
    error: null,
    artifacts: [],
    cost: null,
    ...overrides,
  }
}

describe('mapResponseToGraphState', () => {
  it('returns a single START node with no edges when response is null', () => {
    const result = mapResponseToGraphState(null)
    expect(result.nodes).toHaveLength(1)
    expect(result.nodes[0].id).toBe('START')
    expect(result.edges).toHaveLength(0)
  })

  it('produces START, prep, conductor:1, END for a single iteration done job', () => {
    const conductor: ConductorArtifact = {
      node: 'conductor',
      status: 'done',
      started_at: '2024-01-01T00:00:00Z',
      completed_at: '2024-01-01T00:01:00Z',
      iterations: [
        {
          iteration: 1,
          tool_calls: [],
          findings_count: 0,
          finalize: true,
          reasoning: 'done',
          started_at: '2024-01-01T00:00:01Z',
        },
      ],
    }
    const response = makeResponse({
      status: 'done',
      artifacts: [
        { node: 'prep', status: 'done', started_at: '2024-01-01T00:00:00Z', completed_at: '2024-01-01T00:00:01Z' },
        conductor,
      ],
    })

    const { nodes, edges } = mapResponseToGraphState(response)

    const nodeIds = nodes.map(n => n.id)
    expect(nodeIds).toContain('START')
    expect(nodeIds).toContain('prep')
    expect(nodeIds).toContain('conductor:1')
    expect(nodeIds).toContain('END')

    const conductor1Node = nodes.find(n => n.id === 'conductor:1')!
    expect(conductor1Node.status).toBe('done')

    // Edges: START->prep, prep->conductor:1, conductor:1->END
    expect(edges).toContainEqual({ source: 'START', target: 'prep' })
    expect(edges).toContainEqual({ source: 'prep', target: 'conductor:1' })
    expect(edges).toContainEqual({ source: 'conductor:1', target: 'END' })
  })

  it('assigns laneIndex to tool nodes from a tool_runner iteration', () => {
    const conductor: ConductorArtifact = {
      node: 'conductor',
      status: 'done',
      started_at: '2024-01-01T00:00:00Z',
      completed_at: null,
      iterations: [
        {
          iteration: 1,
          tool_calls: [],
          findings_count: 0,
          finalize: false,
          reasoning: 'run tools',
          started_at: '2024-01-01T00:00:01Z',
        },
      ],
    }
    const toolRunner: ToolRunnerArtifact = {
      node: 'tool_runner',
      status: 'done',
      started_at: '2024-01-01T00:00:02Z',
      completed_at: null,
      iterations: [
        {
          conductor_iteration: 1,
          tools_run: ['npm_audit', 'check_licenses'],
          errors: [],
          started_at: '2024-01-01T00:00:03Z',
        },
      ],
    }
    const response = makeResponse({ artifacts: [conductor, toolRunner] })

    const { nodes } = mapResponseToGraphState(response)

    const npmNode = nodes.find(n => n.id === 'tool:npm_audit:1')!
    const licNode = nodes.find(n => n.id === 'tool:check_licenses:1')!

    expect(npmNode).toBeDefined()
    expect(licNode).toBeDefined()
    expect(npmNode.def.laneIndex).toBe(0)
    expect(licNode.def.laneIndex).toBe(1)
  })

  it('splits conductor iterations around hitl_gate using timestamps', () => {
    const T1 = '2024-01-01T00:01:00Z'
    const T_HITL = '2024-01-01T00:02:00Z'
    const T2 = '2024-01-01T00:03:00Z'

    const conductor: ConductorArtifact = {
      node: 'conductor',
      status: 'running',
      started_at: '2024-01-01T00:00:00Z',
      completed_at: null,
      iterations: [
        { iteration: 1, tool_calls: [], findings_count: 0, finalize: false, reasoning: 'pre', started_at: T1 },
        { iteration: 2, tool_calls: [], findings_count: 0, finalize: false, reasoning: 'post', started_at: T2 },
      ],
    }
    const hitl: HitlGateArtifact = {
      node: 'hitl_gate',
      status: 'done',
      started_at: T_HITL,
      completed_at: null,
      messages: [],
    }
    const response = makeResponse({
      status: 'running',
      artifacts: [conductor, hitl],
    })

    const { nodes, edges } = mapResponseToGraphState(response)

    const nodeIds = nodes.map(n => n.id)
    expect(nodeIds).toContain('conductor:1')
    expect(nodeIds).toContain('hitl_gate')
    expect(nodeIds).toContain('conductor:2')

    // conductor:1 -> hitl_gate -> conductor:2
    expect(edges).toContainEqual({ source: 'conductor:1', target: 'hitl_gate' })
    expect(edges).toContainEqual({ source: 'hitl_gate', target: 'conductor:2' })
  })
})
