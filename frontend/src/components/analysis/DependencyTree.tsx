import { useEffect, useRef } from 'react'
import * as d3 from 'd3'
import { cn } from '../../lib/utils'
import type { DependencyTree as DependencyTreeData, DependencyTreeNode, DepTreeDatum } from '../../api/types'

// ── Augmented D3 node type ─────────────────────────────────────────────────────

type TreeNode = d3.HierarchyPointNode<DepTreeDatum> & {
  _children?: d3.HierarchyPointNode<DepTreeDatum>['children']
  x0?: number
  y0?: number
}

// ── Constants ─────────────────────────────────────────────────────────────────

const NODE_RADIUS = 6
const ROOT_NODE_RADIUS = 9
const MARGIN = { top: 40, right: 80, bottom: 40, left: 80 }
const TRANSITION_DURATION = 300
const DEFAULT_SVG_HEIGHT = 500

// ── Data conversion ───────────────────────────────────────────────────────────

function nodeToDatum(name: string, node: DependencyTreeNode): DepTreeDatum {
  const children = Object.entries(node.deps).map(([childName, childNode]) =>
    nodeToDatum(childName, childNode),
  )
  return {
    name,
    version: node.version,
    circular: node.circular,
    children: children.length > 0 ? children : undefined,
  }
}

function buildTreeDatum(tree: DependencyTreeData, projectName: string): DepTreeDatum {
  return {
    name: projectName,
    version: '',
    children: Object.entries(tree).map(([name, node]) => nodeToDatum(name, node)),
  }
}

// ── CSS variable reader ────────────────────────────────────────────────────────

function cssVar(el: Element, name: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim()
}

// ── Component ─────────────────────────────────────────────────────────────────

interface DependencyTreeProps {
  data: DependencyTreeData
  projectName?: string
  className?: string
  height?: number
}

export function DependencyTree({ data, projectName, className, height }: DependencyTreeProps) {
  const SVG_HEIGHT = height ?? DEFAULT_SVG_HEIGHT
  const containerRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  useEffect(() => {
    const container = containerRef.current
    const svgEl = svgRef.current
    if (!container || !svgEl) return

    // Read theme colors from CSS variables
    const colorBorder = cssVar(container, '--color-border')
    const colorText = cssVar(container, '--color-text')
    const colorMuted = cssVar(container, '--color-muted')
    const colorAccent = cssVar(container, '--color-accent')
    const colorSurface = cssVar(container, '--color-surface-raised')
    const colorError = cssVar(container, '--color-error')

    const width = container.clientWidth || 800

    // Build hierarchy
    const rootDatum = buildTreeDatum(data, projectName || 'root')
    const root = d3.hierarchy<DepTreeDatum>(rootDatum) as TreeNode

    // Tree layout — nodeSize([horizontal-spread, depth-distance])
    const treeLayout = d3.tree<DepTreeDatum>().nodeSize([28, 180])

    // Clear previous render
    d3.select(svgEl).selectAll('*').remove()

    const svg = d3
      .select(svgEl)
      .attr('width', width)
      .attr('height', SVG_HEIGHT)
      .style('overflow', 'hidden')

    // Zoom/pan layer
    const zoomGroup = svg.append('g').attr('class', 'zoom-layer')

    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .on('zoom', (event: d3.D3ZoomEvent<SVGSVGElement, unknown>) => {
        zoomGroup.attr('transform', event.transform.toString())
      })

    svg.call(zoom)

    // Initial view: center root horizontally, place at top
    const innerWidth = width - MARGIN.left - MARGIN.right
    svg.call(zoom.transform, d3.zoomIdentity.translate(MARGIN.left + innerWidth / 2, MARGIN.top))

    const gLinks = zoomGroup.append('g').attr('class', 'links')
    const gNodes = zoomGroup.append('g').attr('class', 'nodes')

    // Collapse all nodes beyond depth 1 (root's grandchildren and deeper)
    function collapse(node: TreeNode) {
      if (node.children) {
        node._children = node.children
        node.children = undefined
        node._children.forEach((c) => collapse(c as TreeNode))
      }
    }

    root.children?.forEach((child) => {
      const c = child as TreeNode
      c.children?.forEach((grandchild) => collapse(grandchild as TreeNode))
    })

    root.x0 = 0
    root.y0 = 0

    // Track selected node for edge highlighting
    let selectedNode: TreeNode | null = null

    // Curved link path generator (top-to-bottom)
    function linkPath(
      src: { x: number; y: number },
      tgt: { x: number; y: number },
    ): string {
      const midY = (src.y + tgt.y) / 2
      return `M${src.x},${src.y} C${src.x},${midY} ${tgt.x},${midY} ${tgt.x},${tgt.y}`
    }

    function isHighlighted(d: d3.HierarchyPointLink<DepTreeDatum>): boolean {
      if (!selectedNode) return false
      return d.source === selectedNode || d.target === selectedNode
    }

    // Drag behavior — moves node in place and redraws connected edges
    const drag = d3
      .drag<SVGGElement, TreeNode>()
      .on('start', function () {
        d3.select(this).raise().style('cursor', 'grabbing')
      })
      .on('drag', function (event, d) {
        d.x += event.dx
        d.y += event.dy
        d3.select(this).attr('transform', `translate(${d.x},${d.y})`)
        gLinks
          .selectAll<SVGPathElement, d3.HierarchyPointLink<DepTreeDatum>>('path.link')
          .filter((l) => l.source === d || l.target === d)
          .attr('d', (l) =>
            linkPath(
              { x: (l.source as TreeNode).x, y: (l.source as TreeNode).y },
              { x: (l.target as TreeNode).x, y: (l.target as TreeNode).y },
            ),
          )
      })
      .on('end', function () {
        d3.select(this).style('cursor', 'grab')
      })

    function update(clickedNode: TreeNode) {
      treeLayout(root)

      const nodes = root.descendants() as TreeNode[]
      const links = root.links() as d3.HierarchyPointLink<DepTreeDatum>[]

      const t = d3.transition().duration(TRANSITION_DURATION).ease(d3.easeCubicInOut)

      // ── Links ──────────────────────────────────────────────────────────────
      const link = gLinks
        .selectAll<SVGPathElement, d3.HierarchyPointLink<DepTreeDatum>>('path.link')
        .data(links, (d) => (d.target as TreeNode).data.name + (d.target as TreeNode).data.version)

      const linkEnter = link
        .enter()
        .append('path')
        .attr('class', 'link')
        .attr('fill', 'none')
        .attr('stroke', (d) => (isHighlighted(d) ? colorAccent : colorBorder))
        .attr('stroke-width', (d) => (isHighlighted(d) ? 2.5 : 1.5))
        .attr('stroke-opacity', 0.7)
        .attr('d', () => {
          const ox = clickedNode.x0 ?? clickedNode.x
          const oy = clickedNode.y0 ?? clickedNode.y
          return linkPath({ x: ox, y: oy }, { x: ox, y: oy })
        })

      link
        .merge(linkEnter)
        .transition(t)
        .attr('stroke', (d) => (isHighlighted(d) ? colorAccent : colorBorder))
        .attr('stroke-width', (d) => (isHighlighted(d) ? 2.5 : 1.5))
        .attr('d', (d) =>
          linkPath(
            { x: (d.source as TreeNode).x, y: (d.source as TreeNode).y },
            { x: (d.target as TreeNode).x, y: (d.target as TreeNode).y },
          ),
        )

      link
        .exit()
        .transition(t)
        .attr('d', () => linkPath({ x: clickedNode.x, y: clickedNode.y }, { x: clickedNode.x, y: clickedNode.y }))
        .remove()

      // ── Nodes ──────────────────────────────────────────────────────────────
      const node = gNodes
        .selectAll<SVGGElement, TreeNode>('g.node')
        .data(nodes, (d) => d.data.name + d.data.version)

      const nodeEnter = node
        .enter()
        .append('g')
        .attr('class', 'node')
        .attr('transform', () => {
          const ox = clickedNode.x0 ?? clickedNode.x
          const oy = clickedNode.y0 ?? clickedNode.y
          return `translate(${ox},${oy})`
        })
        .style('cursor', 'grab')
        .on('click', (_event, d) => {
          const n = d as TreeNode
          // Toggle edge highlight selection
          selectedNode = selectedNode === n ? null : n
          // Toggle expand/collapse
          if (n._children) {
            n.children = n._children
            n._children = undefined
          } else if (n.children) {
            n._children = n.children
            n.children = undefined
          }
          n.x0 = n.x
          n.y0 = n.y
          update(n)
        })
        .call(drag)

      // Circular dep indicator ring
      nodeEnter
        .filter((d) => !!d.data.circular)
        .append('circle')
        .attr('r', NODE_RADIUS + 4)
        .attr('fill', 'none')
        .attr('stroke', colorError)
        .attr('stroke-width', 1.5)
        .attr('stroke-dasharray', '3 2')
        .attr('opacity', 0.85)

      // Main node circle
      nodeEnter
        .append('circle')
        .attr('class', 'node-circle')
        .attr('r', (d) => (d.depth === 0 ? ROOT_NODE_RADIUS : NODE_RADIUS))
        .attr('fill', colorSurface)
        .attr('stroke', (d) => {
          const n = d as TreeNode
          if (d.depth === 0) return colorAccent
          if (n._children) return colorAccent
          return colorBorder
        })
        .attr('stroke-width', (d) => (d.depth === 0 ? 2.5 : 1.5))

      // Package name label
      nodeEnter
        .append('text')
        .attr('class', 'node-name')
        .attr('dy', '-0.9em')
        .attr('text-anchor', 'middle')
        .attr('fill', (d) => (d.depth === 0 ? colorAccent : colorText))
        .attr('font-family', 'monospace')
        .attr('font-size', '10px')
        .attr('font-weight', (d) => (d.depth === 0 ? '600' : '400'))
        .text((d) => d.data.name)

      // Version label
      nodeEnter
        .append('text')
        .attr('class', 'node-version')
        .attr('dy', '1.9em')
        .attr('text-anchor', 'middle')
        .attr('fill', colorMuted)
        .attr('font-family', 'monospace')
        .attr('font-size', '9px')
        .text((d) => (d.depth === 0 ? '' : d.data.version))

      // Dot inside collapsed nodes
      nodeEnter
        .append('circle')
        .attr('class', 'collapse-dot')
        .attr('r', 2.5)
        .attr('fill', colorAccent)
        .attr('pointer-events', 'none')
        .attr('opacity', (d) => ((d as TreeNode)._children ? 1 : 0))

      // Transition all nodes to new positions
      const nodeUpdate = node.merge(nodeEnter)

      nodeUpdate.transition(t).attr('transform', (d) => `translate(${d.x},${d.y})`)

      // Update circle stroke based on collapse state
      nodeUpdate
        .select<SVGCircleElement>('circle.node-circle')
        .transition(t)
        .attr('stroke', (d) => {
          const n = d as TreeNode
          if (d.depth === 0) return colorAccent
          if (n._children) return colorAccent
          return colorBorder
        })

      // Update collapse dot visibility
      nodeUpdate
        .select<SVGCircleElement>('circle.collapse-dot')
        .attr('opacity', (d) => ((d as TreeNode)._children ? 1 : 0))

      // Update cursor
      nodeUpdate.style('cursor', 'grab')

      // Exit animation
      const nodeExit = node
        .exit()
        .transition(t)
        .attr('transform', `translate(${clickedNode.x},${clickedNode.y})`)

      nodeExit.select('circle').attr('r', 0)
      nodeExit.select('text').attr('opacity', 0)
      nodeExit.remove()

      // Save positions for next transition
      nodes.forEach((d) => {
        d.x0 = d.x
        d.y0 = d.y
      })
    }

    update(root)

    return () => {
      svg.on('.zoom', null)
      d3.select(svgEl).selectAll('*').remove()
    }
  }, [data, projectName, SVG_HEIGHT])

  return (
    <div ref={containerRef} className={cn('relative w-full', className)}>
      <svg
        ref={svgRef}
        className="w-full"
        style={{ height: `${SVG_HEIGHT}px`, display: 'block' }}
        aria-label="Dependency tree visualization"
        role="img"
      />
      <p className="absolute right-3 bottom-3 select-none font-mono text-[10px] text-[--color-muted]">
        scroll to zoom · drag node to reposition · click node to expand/highlight edges
      </p>
    </div>
  )
}
