# Spec: Dependency Tree Visualization

## Overview

Add a D3.js SVG collapsible dependency tree visualization to the Job Detail page. The backend already computes and returns a `dependency_tree` field in the job result — a nested dict representing the full package dependency hierarchy with cycle detection. This feature makes that data visible in an interactive graph.

## User Experience

- The **Dependency Tree** section appears on the Job Detail page between _Manifest Files_ and _Raw JSON_
- The tree grows top-to-bottom: direct dependencies at the top, transitive below
- Click any node to collapse or expand its subtree (animated)
- Scroll to zoom, drag to pan
- Circular dependencies (packages that form a cycle, already detected and marked by the backend) show a dashed red ring
- Nodes with hidden children show an accent-colored border as a visual cue

## Data Flow

The backend stores the full dependency tree in the job result:

```
GET /analyze/{trace_id}
  → result.dependency_tree: DependencyTree
```

The `dependency_tree` shape:

```json
{
  "react": {
    "version": "18.2.0",
    "deps": {
      "loose-envify": {
        "version": "1.4.0",
        "deps": {}
      }
    }
  },
  "lodash": {
    "version": "4.17.21",
    "deps": {},
    "circular": true
  }
}
```

Circular dependencies are already resolved by the backend: a node with `circular: true` has empty `deps`, so the data forms a valid tree (no infinite recursion).

## Implementation

### Packages

```bash
pnpm add d3
pnpm add -D @types/d3
```

### New types (`frontend/src/api/types.ts`)

```typescript
export interface DependencyTreeNode {
  version: string
  deps: Record<string, DependencyTreeNode>
  circular?: boolean
}

export type DependencyTree = Record<string, DependencyTreeNode>

// Internal shape consumed by d3.hierarchy()
export interface DepTreeDatum {
  name: string
  version: string
  circular?: boolean
  children?: DepTreeDatum[]
}
```

`DiscoveryResult` gains `dependency_tree?: DependencyTree`.

### New component (`frontend/src/components/analysis/DependencyTree.tsx`)

Architecture:

| Element | Description |
|---|---|
| `buildTreeDatum(tree)` | Converts `DependencyTree` (Record) → single `DepTreeDatum` with synthetic `root` node. D3 `hierarchy()` requires exactly one root. |
| `cssVar(name)` | Reads CSS custom properties from `:root` at render time so SVG colors match the app theme. |
| `TreeNode` type | Augments `d3.HierarchyPointNode<DepTreeDatum>` with `_children`, `x0`, `y0` for collapsible/transition state. |
| `useEffect + useRef` | D3 owns the SVG DOM; React owns the container div. Full rebuild on `data` prop change. |
| `update(clickedNode)` | Re-runs `treeLayout(root)` + transitions nodes/links. Called on click and on initial render. |

Layout: `d3.tree().nodeSize([28, 160])` — top-to-bottom, fixed spacing.

Collapse defaults: root + first level expanded; all deeper nodes collapsed.

Zoom: `d3.zoom()` with `scaleExtent([0.1, 4])`.

Circular indicator: dashed red ring circle drawn before the main node circle.

Colors: all read from `--color-border`, `--color-text`, `--color-muted`, `--color-accent`, `--color-surface-raised`, `--color-error`.

### Integration (`frontend/src/components/analysis/AnalysisResult.tsx`)

Add import:
```typescript
import { DependencyTree } from './DependencyTree'
```

Add section between manifest files (section 4) and raw JSON (section 5):
```tsx
{result?.dependency_tree && Object.keys(result.dependency_tree).length > 0 && (
  <div className="space-y-2">
    <p className="font-mono text-xs tracking-widest text-[--color-muted] uppercase">
      Dependency Tree
    </p>
    <div className="overflow-hidden rounded border border-[--color-border] bg-[--color-surface-raised]">
      <DependencyTree data={result.dependency_tree} />
    </div>
  </div>
)}
```

## Files Changed

| File | Change |
|---|---|
| `frontend/package.json` | Add `d3` dependency |
| `frontend/src/api/types.ts` | Add 3 types, extend `DiscoveryResult` |
| `frontend/src/components/analysis/DependencyTree.tsx` | New component |
| `frontend/src/components/analysis/AnalysisResult.tsx` | Import + render tree section |
