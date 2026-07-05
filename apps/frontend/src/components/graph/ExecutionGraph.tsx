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

const NODE_W = 132
const NODE_H = 36
const NODE_RX = 5
const CIRCLE_R = 22 // radius for terminal (START / END) nodes
const SUBGRAPH_GAP = 12 // horizontal gap between adjacent subgraph rects
const SVG_HEIGHT = 720
const V_MARGIN = 24 // bottom padding below last node
const LAYER_START_Y = 44
const LAYER_SPACING = 100

// ── Helpers ────────────────────────────────────────────────────────────────────

function cssVar(el: Element, name: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim()
}

interface NodePos {
  x: number
  y: number
}

function computePositions(nodes: GraphNodeState[], containerWidth: number): Map<NodeId, NodePos> {
  const positions = new Map<NodeId, NodePos>()

  const uniqueLayers = [...new Set(nodes.map((n) => n.def.layer))].sort((a, b) => a - b)
  const layerY = new Map<number, number>()
  uniqueLayers.forEach((layer, idx) => {
    layerY.set(layer, LAYER_START_Y + idx * LAYER_SPACING)
  })

  const subgraphsByLayer = new Map<number, GraphNodeState[]>()
  for (const node of nodes) {
    if (node.def.isSubgraph) {
      const group = subgraphsByLayer.get(node.def.layer) ?? []
      group.push(node)
      subgraphsByLayer.set(node.def.layer, group)
    }
  }
  for (const [layer, group] of subgraphsByLayer) {
    subgraphsByLayer.set(
      layer,
      [...group].sort((a, b) => (a.def.laneIndex ?? 0) - (b.def.laneIndex ?? 0)),
    )
  }

  const layerSubX0 = new Map<number, number>()
  for (const [layer, group] of subgraphsByLayer) {
    const totalW = group.length * NODE_W + Math.max(0, group.length - 1) * SUBGRAPH_GAP
    layerSubX0.set(layer, (containerWidth - totalW) / 2 + NODE_W / 2)
  }

  for (const node of nodes) {
    const y = layerY.get(node.def.layer) ?? LAYER_START_Y
    let x: number
    if (node.def.isSubgraph) {
      const group = subgraphsByLayer.get(node.def.layer)!
      const idxInGroup = group.findIndex((n) => n.id === node.id)
      x = layerSubX0.get(node.def.layer)! + idxInGroup * (NODE_W + SUBGRAPH_GAP)
    } else {
      x = containerWidth / 2
    }
    positions.set(node.id, { x, y })
  }

  return positions
}

function statusStrokeColor(
  status: NodeStatus,
  colors: { border: string; accent: string; done: string; error: string; running: string; awaiting: string; cancelled: string },
): string {
  switch (status) {
    case 'active':    return colors.running
    case 'awaiting':  return colors.awaiting
    case 'done':      return colors.done
    case 'failed':    return colors.error
    case 'cancelled': return colors.cancelled
    default:          return colors.border
  }
}

function statusDotFill(
  status: NodeStatus,
  colors: { accent: string; done: string; error: string; running: string; awaiting: string },
): string {
  switch (status) {
    case 'active':   return colors.running
    case 'awaiting': return colors.awaiting
    case 'done':     return colors.done
    case 'failed':   return colors.error
    default:         return 'transparent'
  }
}

/** Vertical cubic bezier: exits bottom of source, enters top of target. */
function edgePath(sp: NodePos, tp: NodePos, srcIsCircle: boolean, tgtIsCircle: boolean): string {
  const sx = sp.x
  const sy = sp.y + (srcIsCircle ? CIRCLE_R : NODE_H / 2)
  const tx = tp.x
  const ty = tp.y - (tgtIsCircle ? CIRCLE_R : NODE_H / 2)
  const midY = (sy + ty) / 2
  return `M${sx},${sy} C${sx},${midY} ${tx},${midY} ${tx},${ty}`
}

// ── Component ─────────────────────────────────────────────────────────────────

interface ExecutionGraphProps {
  renderData: GraphRenderData
  selectedNodeId: NodeId | null
  onNodeClick: (id: NodeId | null) => void
  isRunning?: boolean
  className?: string
}

export function ExecutionGraph({
  renderData,
  selectedNodeId,
  onNodeClick,
  isRunning = false,
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
      border:    cssVar(container, '--color-border'),
      text:      cssVar(container, '--color-text'),
      muted:     cssVar(container, '--color-muted'),
      accent:    cssVar(container, '--color-accent'),
      surface:   cssVar(container, '--color-surface-raised'),
      error:     cssVar(container, '--color-error'),
      done:      '#34c785',
      running:   '#eab308',
      awaiting:  '#f5a623',   // amber — same hue as accent, used for pulsing border
      cancelled: '#3f4152',   // muted dark grey
    }

    const width = container.clientWidth || 900
    const positions = computePositions(renderData.nodes, width)

    // Clear previous render
    d3.select(svgEl).selectAll('*').remove()

    const svg = d3
      .select(svgEl)
      .attr('width', width)
      .attr('height', SVG_HEIGHT)
      .style('overflow', 'hidden')

    // Defs: arrowheads + pulse/flow keyframes
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
          '.node-pulse { animation: nodePulse 1.8s ease-in-out infinite; }' +
          '@keyframes borderPulse { 0%,100%{stroke-opacity:1} 50%{stroke-opacity:0.3} }' +
          '.node-awaiting { animation: borderPulse 1.4s ease-in-out infinite; }' +
          '@keyframes edgeFlow { from { stroke-dashoffset: 20 } to { stroke-dashoffset: 0 } }' +
          '.edge-flow { animation: edgeFlow 0.6s linear infinite; }',
      )

    makeArrow('arrow-flow', colors.running)

    // Zoom/pan layer
    const zoomGroup = svg.append('g').attr('class', 'zoom-layer')

    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.25, 3])
      .on('zoom', (event: d3.D3ZoomEvent<SVGSVGElement, unknown>) => {
        zoomGroup.attr('transform', event.transform.toString())
      })

    svg.call(zoom)

    // Fit graph vertically; re-center horizontally after scale
    const uniqueLayersSorted = [...new Set(renderData.nodes.map((n) => n.def.layer))].sort(
      (a, b) => a - b,
    )
    const maxLayerY = LAYER_START_Y + (uniqueLayersSorted.length - 1) * LAYER_SPACING
    const graphHeight = maxLayerY + CIRCLE_R + V_MARGIN
    const initialScale = Math.min(0.98, SVG_HEIGHT / graphHeight)
    const xOffset = (width * (1 - initialScale)) / 2
    const yOffset = (SVG_HEIGHT - graphHeight * initialScale) / 2
    svg.call(zoom.transform, d3.zoomIdentity.translate(xOffset, yOffset).scale(initialScale))

    // ── Edges ──────────────────────────────────────────────────────────────────
    const gEdges = zoomGroup.append('g').attr('class', 'edges')

    const nodeStatusMap = new Map(renderData.nodes.map((n) => [n.id, n.status]))

    const edgeIsHighlighted = (e: GraphEdgeDef) =>
      e.source === selectedNodeId || e.target === selectedNodeId

    const edgeIsFlowing = (e: GraphEdgeDef) => {
      if (!isRunning) return false
      const srcStatus = nodeStatusMap.get(e.source)
      const tgtStatus = nodeStatusMap.get(e.target)
      return srcStatus === 'done' && tgtStatus !== 'done' && tgtStatus !== 'failed'
    }

    const terminalIds = new Set<NodeId>(['START', 'END'])

    for (const edge of renderData.edges) {
      const sp = positions.get(edge.source)
      const tp = positions.get(edge.target)
      if (!sp || !tp) continue

      const highlighted = edgeIsHighlighted(edge)
      const flowing = !highlighted && edgeIsFlowing(edge)

      const path = gEdges
        .append('path')
        .attr('d', edgePath(sp, tp, terminalIds.has(edge.source), terminalIds.has(edge.target)))
        .attr('fill', 'none')

      if (highlighted) {
        path
          .attr('stroke', colors.accent)
          .attr('stroke-width', 2)
          .attr('stroke-opacity', 0.9)
          .attr('marker-end', 'url(#arrow-accent)')
      } else if (flowing) {
        path
          .attr('stroke', colors.running)
          .attr('stroke-width', 1.5)
          .attr('stroke-opacity', 0.8)
          .attr('stroke-dasharray', '6 4')
          .attr('class', 'edge-flow')
          .attr('marker-end', 'url(#arrow-flow)')
      } else {
        path
          .attr('stroke', colors.border)
          .attr('stroke-width', 1.5)
          .attr('stroke-opacity', 0.55)
          .attr('marker-end', 'url(#arrow-default)')
      }
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
      const filterAttr = isSelected ? `drop-shadow(0 0 6px ${colors.accent}50)` : null

      const g = gNodes
        .append('g')
        .attr('class', `node node-${node.id}`)
        .attr('transform', `translate(${pos.x},${pos.y})`)
        .style('cursor', hasPanel ? 'pointer' : 'default')
        .on('click', () => {
          onNodeClickRef.current(node.id === selectedNodeId ? null : node.id)
        })

      if (isTerminal) {
        // Circle shape for START / END
        g.append('circle')
          .attr('r', CIRCLE_R)
          .attr('fill', colors.surface)
          .attr('stroke', strokeColor)
          .attr('stroke-width', strokeWidth)
          .attr('filter', filterAttr)

        g.append('text')
          .attr('text-anchor', 'middle')
          .attr('dominant-baseline', 'middle')
          .attr('fill', colors.muted)
          .attr('font-family', 'monospace')
          .attr('font-size', '8px')
          .attr('letter-spacing', '0.08em')
          .attr('pointer-events', 'none')
          .text(node.def.label)
      } else {
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
          .attr('filter', filterAttr)

        if (node.status === 'awaiting') {
          g.select('rect').attr('class', 'node-awaiting')
        }

        // Label
        g.append('text')
          .attr('text-anchor', 'middle')
          .attr('dominant-baseline', 'middle')
          .attr('fill', isSelected ? colors.accent : colors.text)
          .attr('font-family', 'monospace')
          .attr('font-size', '9px')
          .attr('font-weight', isSelected ? '600' : '400')
          .attr('pointer-events', 'none')
          .attr('letter-spacing', '0.02em')
          .text(node.def.label)
      }

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
        } else if (node.status === 'awaiting') {
          dot.attr('class', 'node-pulse')  // also pulse
        } else if (node.status === 'cancelled') {
          dot.attr('opacity', 0.3)
        }
      }
    }

    return () => {
      svg.on('.zoom', null)
      d3.select(svgEl).selectAll('*').remove()
    }
  }, [renderData, selectedNodeId, isRunning])

  return (
    <div ref={containerRef} className={cn('relative w-full', className)}>
      <div className="overflow-hidden rounded-lg border border-(--color-border) bg-(--color-surface)">
        <svg
          ref={svgRef}
          className="w-full"
          style={{ height: `${SVG_HEIGHT}px`, display: 'block' }}
          aria-label="Pipeline execution graph"
          role="img"
        />
      </div>
      <p className="mt-1.5 text-right font-mono text-[10px] text-(--color-muted) select-none">
        scroll to zoom · click node to inspect
      </p>
    </div>
  )
}
