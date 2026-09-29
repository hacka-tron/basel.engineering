import { Handle, MarkerType, Position, ReactFlow, type NodeProps } from '@xyflow/react'
import type { CSSProperties } from 'react'
import '@xyflow/react/dist/style.css'
import { architectureEdges, architectureNodes } from '../architecture'

const handleStyle: CSSProperties = {
  width: 1,
  height: 1,
  border: 0,
  background: 'transparent',
  opacity: 0,
}

const flowStyle = {
  '--xy-background-color': 'var(--color-panel)',
  '--xy-edge-stroke': 'var(--color-muted)',
  '--xy-edge-stroke-selected': 'var(--color-cyan)',
  '--xy-attribution-background-color': 'var(--color-panel)',
} as CSSProperties

function ArchitectureNodeView({ data }: NodeProps) {
  return (
    <div className="flex h-[42px] w-[124px] items-center justify-center rounded-[3px] border border-hairline bg-panel px-2 text-center text-xs text-primary">
      <Handle id="left" type="target" position={Position.Left} style={handleStyle} />
      <Handle id="top" type="target" position={Position.Top} style={handleStyle} />
      {data.label as string}
      <Handle id="right" type="source" position={Position.Right} style={handleStyle} />
      <Handle id="bottom" type="source" position={Position.Bottom} style={handleStyle} />
    </div>
  )
}

const nodeTypes = { architecture: ArchitectureNodeView }
const defaultEdgeOptions = {
  type: 'smoothstep',
  markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12, color: 'var(--color-muted)' },
}

function ArchitecturePanel() {
  return (
    <section aria-label="Architecture" className="flex min-h-0 min-w-0 flex-col bg-panel">
      <div className="min-h-0 flex-1">
        <ReactFlow
          aria-label="System architecture diagram"
          nodes={architectureNodes}
          edges={architectureEdges}
          defaultEdgeOptions={defaultEdgeOptions}
          nodeTypes={nodeTypes}
          colorMode="dark"
          style={flowStyle}
          fitView
          fitViewOptions={{ padding: 0.12, maxZoom: 1 }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          zoomOnScroll={false}
          zoomOnDoubleClick={false}
          proOptions={{ hideAttribution: true }}
        />
      </div>
      <div className="h-36 shrink-0 border-t border-hairline px-7 py-5">
        <h2 className="text-xs font-medium text-primary">Retrieved chunks</h2>
        <p className="mt-4 text-xs text-muted">No query yet.</p>
      </div>
    </section>
  )
}

export default ArchitecturePanel
