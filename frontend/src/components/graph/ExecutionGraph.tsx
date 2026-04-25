import { useEffect, useRef } from 'react'
import * as d3 from 'd3'
import { cn } from '../../lib/utils'
import type {
  GraphRenderData,
  GraphNodeState,
  GraphEdgeDef,
  NodeId,
  NodeStatus,
} from './graphDefinition'
import { getPanelComponent } from './nodeRegistry'

// ── Layout constants ───────────────────────────────────────────────────────────

const LAYER_X: Record<number, number> = {
  0: 55,
  1: 210,
  2: 375,
  3: 545,
  4: 715,
  5: 850,
}

const NODE_W = 132
const NODE_H = 36
const NODE_RX = 5
const SVG_HEIGHT = 320
const LANE_H = 52 // vertical gap between subgraph node centers
const MARGIN_R = 30 // right margin before clip

// ── Helpers ────────────────────────────────────────────────────────────────────

function cssVar(el: Element, name: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim()
}

interface NodePos {
  x: number
  y: number
}

function computePositions(nodes: GraphNodeState[]): Map<NodeId, NodePos> {
  const positions = new Map<NodeId, NodePos>()
  const subgraphs = nodes
    .filter((n) => n.def.isSubgraph)
    .sort((a, b) => (a.def.laneIndex ?? 0) - (b.def.laneIndex ?? 0))

  const numSub = subgraphs.length
  const totalSubH = Math.max(0, numSub - 1) * LANE_H
  const subY0 = SVG_HEIGHT / 2 - totalSubH / 2

  let subIdx = 0
  for (const node of nodes) {
    const x = LAYER_X[node.def.layer] ?? 0
    const y = node.def.isSubgraph ? subY0 + subIdx++ * LANE_H : SVG_HEIGHT / 2
    positions.set(node.id, { x, y })
  }
  return positions
}

function statusStrokeColor(
  status: NodeStatus,
  colors: { border: string; accent: string; done: string; error: string; running: string },
): string {
  switch (status) {
    case 'active':
      return colors.running
    case 'done':
      return colors.done
    case 'failed':
      return colors.error
    default:
      return colors.border
  }
}

function statusDotFill(
  status: NodeStatus,
  colors: { accent: string; done: string; error: string; running: string },
): string {
  switch (status) {
    case 'active':
      return colors.running
    case 'done':
      return colors.done
    case 'failed':
      return colors.error
    default:
      return 'transparent'
  }
}

function edgePath(sp: NodePos, tp: NodePos): string {
  const sx = sp.x + NODE_W / 2
  const sy = sp.y
  const tx = tp.x - NODE_W / 2
  const ty = tp.y
  const midX = (sx + tx) / 2
  return `M${sx},${sy} C${midX},${sy} ${midX},${ty} ${tx},${ty}`
}

// ── Component ─────────────────────────────────────────────────────────────────

interface ExecutionGraphProps {
  renderData: GraphRenderData
  selectedNodeId: NodeId | null
  onNodeClick: (id: NodeId | null) => void
  className?: string
}

export function ExecutionGraph({
  renderData,
  selectedNodeId,
  onNodeClick,
  className,
}: ExecutionGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  // Stable ref so the D3 handler always calls the latest onNodeClick
  const onNodeClickRef = useRef(onNodeClick)
  useEffect(() => {
    onNodeClickRef.current = onNodeClick
  }, [onNodeClick])

  useEffect(() => {
    const container = containerRef.current
    const svgEl = svgRef.current
    if (!container || !svgEl) return

    const colors = {
      border: cssVar(container, '--color-border'),
      text: cssVar(container, '--color-text'),
      muted: cssVar(container, '--color-muted'),
      accent: cssVar(container, '--color-accent'),
      surface: cssVar(container, '--color-surface-raised'),
      error: cssVar(container, '--color-error'),
      done: '#34c785', // green
      running: '#eab308', // yellow-500
    }

    const width = container.clientWidth || 900
    const positions = computePositions(renderData.nodes)

    // Clear previous render
    d3.select(svgEl).selectAll('*').remove()

    const svg = d3
      .select(svgEl)
      .attr('width', width)
      .attr('height', SVG_HEIGHT)
      .style('overflow', 'hidden')

    // Defs: arrowheads + pulse keyframes
    const defs = svg.append('defs')

    const makeArrow = (id: string, fill: string) =>
      defs
        .append('marker')
        .attr('id', id)
        .attr('markerWidth', 8)
        .attr('markerHeight', 6)
        .attr('refX', 8)
        .attr('refY', 3)
        .attr('orient', 'auto')
        .append('path')
        .attr('d', 'M0,0 L8,3 L0,6 Z')
        .attr('fill', fill)

    makeArrow('arrow-default', colors.border)
    makeArrow('arrow-accent', colors.accent)

    defs
      .append('style')
      .text(
        '@keyframes nodePulse { 0%,100%{opacity:1} 50%{opacity:0.3} }' +
          '.node-pulse { animation: nodePulse 1.8s ease-in-out infinite; }',
      )

    // Zoom/pan layer
    const zoomGroup = svg.append('g').attr('class', 'zoom-layer')

    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.25, 3])
      .on('zoom', (event: d3.D3ZoomEvent<SVGSVGElement, unknown>) => {
        zoomGroup.attr('transform', event.transform.toString())
      })

    svg.call(zoom)

    // Fit graph in container on first render
    const graphWidth = (LAYER_X[5] ?? 850) + NODE_W / 2 + MARGIN_R
    const initialScale = Math.min(1, width / graphWidth)
    const xOffset = (width - graphWidth * initialScale) / 2
    svg.call(zoom.transform, d3.zoomIdentity.translate(xOffset, 0).scale(initialScale))

    // ── Edges ──────────────────────────────────────────────────────────────────
    const gEdges = zoomGroup.append('g').attr('class', 'edges')

    const edgeIsHighlighted = (e: GraphEdgeDef) =>
      e.source === selectedNodeId || e.target === selectedNodeId

    for (const edge of renderData.edges) {
      const sp = positions.get(edge.source)
      const tp = positions.get(edge.target)
      if (!sp || !tp) continue

      const highlighted = edgeIsHighlighted(edge)

      gEdges
        .append('path')
        .attr('d', edgePath(sp, tp))
        .attr('fill', 'none')
        .attr('stroke', highlighted ? colors.accent : colors.border)
        .attr('stroke-width', highlighted ? 2 : 1.5)
        .attr('stroke-opacity', highlighted ? 0.9 : 0.55)
        .attr('marker-end', highlighted ? 'url(#arrow-accent)' : 'url(#arrow-default)')
    }

    // ── Nodes ──────────────────────────────────────────────────────────────────
    const gNodes = zoomGroup.append('g').attr('class', 'nodes')

    for (const node of renderData.nodes) {
      const pos = positions.get(node.id)
      if (!pos) continue

      const isSelected = node.id === selectedNodeId
      const isTerminal = node.id === 'START' || node.id === 'END'
      const hasPanel = !!getPanelComponent(node.id)

      const strokeColor = isSelected ? colors.accent : statusStrokeColor(node.status, colors)
      const strokeWidth = isSelected || node.status !== 'idle' ? 2.5 : 1.5

      const g = gNodes
        .append('g')
        .attr('class', `node node-${node.id}`)
        .attr('transform', `translate(${pos.x},${pos.y})`)
        .style('cursor', hasPanel ? 'pointer' : 'default')
        .on('click', () => {
          onNodeClickRef.current(node.id === selectedNodeId ? null : node.id)
        })

      // Background rect
      g.append('rect')
        .attr('x', -NODE_W / 2)
        .attr('y', -NODE_H / 2)
        .attr('width', NODE_W)
        .attr('height', NODE_H)
        .attr('rx', NODE_RX)
        .attr('fill', colors.surface)
        .attr('stroke', strokeColor)
        .attr('stroke-width', strokeWidth)
        .attr('filter', isSelected ? `drop-shadow(0 0 6px ${colors.accent}50)` : null)

      // Label
      g.append('text')
        .attr('text-anchor', 'middle')
        .attr('dominant-baseline', 'middle')
        .attr('fill', isTerminal ? colors.muted : isSelected ? colors.accent : colors.text)
        .attr('font-family', 'monospace')
        .attr('font-size', '9px')
        .attr('font-weight', isSelected ? '600' : '400')
        .attr('pointer-events', 'none')
        .attr('letter-spacing', '0.02em')
        .text(node.def.label)

      // Status dot (top-right corner of rect)
      if (!isTerminal) {
        const dotX = NODE_W / 2 - 9
        const dotY = -NODE_H / 2 + 8

        const dot = g
          .append('circle')
          .attr('cx', dotX)
          .attr('cy', dotY)
          .attr('r', 4)
          .attr('fill', statusDotFill(node.status, colors))
          .attr('pointer-events', 'none')

        if (node.status === 'idle') {
          dot.attr('opacity', 0)
        } else if (node.status === 'active') {
          dot.attr('class', 'node-pulse')
        }
      }
    }

    return () => {
      svg.on('.zoom', null)
      d3.select(svgEl).selectAll('*').remove()
    }
  }, [renderData, selectedNodeId])

  return (
    <div ref={containerRef} className={cn('relative w-full', className)}>
      <div className="overflow-hidden rounded-lg border border-[--color-border] bg-[--color-surface]">
        <svg
          ref={svgRef}
          className="w-full"
          style={{ height: `${SVG_HEIGHT}px`, display: 'block' }}
          aria-label="Pipeline execution graph"
          role="img"
        />
      </div>
      <p className="mt-1.5 text-right font-mono text-[10px] text-[--color-muted] select-none">
        scroll to zoom · click node to inspect
      </p>
    </div>
  )
}
