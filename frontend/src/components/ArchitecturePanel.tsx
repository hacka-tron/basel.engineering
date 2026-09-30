import { Handle, MarkerType, Position, ReactFlow, type Node, type NodeProps } from '@xyflow/react'
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import '@xyflow/react/dist/style.css'
import { architectureEdges, architectureNodes, type NodeId } from '../architecture'
import type { RetrievalChunk } from '../lib/sse'

type LiveNode = Node<{
  id: NodeId
  label: string
  implementation: string
  active: boolean
  selected: boolean
  cache?: 'hit' | 'miss'
  onPreview: (id: NodeId) => void
  onLeave: () => void
  onInspect: (id: NodeId) => void
}, 'architecture'>

type ArchitecturePanelProps = {
  activeNode?: NodeId | null
  nodeCacheStatus?: Partial<Record<NodeId, 'hit' | 'miss'>>
  retrievedChunks?: RetrievalChunk[]
  selectedNode?: NodeId | null
  onPreview: () => void
  onInspect: (id: NodeId) => void
}

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

function ArchitectureNodeView({ data }: NodeProps<LiveNode>) {
  return (
    <div
      className={`relative h-[42px] w-[124px] rounded-[3px] border bg-panel text-center text-xs hover:ring-2 hover:ring-cyan focus-within:ring-2 focus-within:ring-cyan ${data.active ? 'border-cyan text-cyan' : 'border-hairline text-primary'} ${data.selected ? 'ring-2 ring-cyan' : ''}`}
    >
      <Handle id="left" type="target" position={Position.Left} style={handleStyle} />
      <Handle id="top" type="target" position={Position.Top} style={handleStyle} />
      <button
        type="button"
        aria-label={`Explore ${data.label}: ${data.implementation}`}
        aria-pressed={data.selected}
        title={`${data.label} · ${data.implementation}`}
        onFocus={() => data.onPreview(data.id)}
        onBlur={data.onLeave}
        onClick={() => data.onInspect(data.id)}
        className="nopan nodrag flex h-full w-full cursor-pointer flex-col items-center justify-center px-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan"
      >
        {data.label}
        {data.cache && <span className="text-[10px] text-muted">{data.cache}</span>}
      </button>
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

function ArchitecturePanel({ activeNode, nodeCacheStatus, retrievedChunks = [], selectedNode, onPreview, onInspect }: ArchitecturePanelProps) {
  const [hoveredNode, setHoveredNode] = useState<NodeId | null>(null)
  const inspectorRef = useRef<HTMLDivElement>(null)
  const previewRef = useRef(onPreview)
  const inspectRef = useRef(onInspect)
  useEffect(() => {
    previewRef.current = onPreview
    inspectRef.current = onInspect
  }, [onPreview, onInspect])
  const previewNode = useCallback((id: NodeId) => { setHoveredNode(id); previewRef.current() }, [])
  const leaveNode = useCallback(() => setHoveredNode(null), [])
  const inspectNode = useCallback((id: NodeId) => inspectRef.current(id), [])
  const inspectedNode = hoveredNode ?? selectedNode
  const inspectedComponent = architectureNodes.find((node) => node.id === inspectedNode)
  useEffect(() => {
    if (inspectorRef.current) inspectorRef.current.scrollTop = 0
  }, [inspectedNode])
  const nodes = useMemo<LiveNode[]>(() => architectureNodes.map((node) => ({
    ...node,
    data: {
      ...node.data,
      id: node.id,
      active: node.id === activeNode,
      selected: node.id === selectedNode,
      cache: nodeCacheStatus?.[node.id],
      onPreview: previewNode,
      onLeave: leaveNode,
      onInspect: inspectNode,
    },
  })), [activeNode, inspectNode, leaveNode, nodeCacheStatus, previewNode, selectedNode])

  return (
    <section aria-label="Architecture" className="flex min-h-0 min-w-0 flex-col bg-panel">
      <div className="min-h-0 flex-1">
        <ReactFlow
          aria-label="System architecture diagram"
          nodes={nodes}
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
          onNodeMouseEnter={(_event, node) => previewNode(node.id as NodeId)}
          onNodeMouseLeave={leaveNode}
          zoomOnScroll={false}
          zoomOnDoubleClick={false}
          proOptions={{ hideAttribution: true }}
        />
      </div>
      <div ref={inspectorRef} className="h-44 shrink-0 overflow-y-auto border-t border-hairline px-7 py-4">
        {inspectedComponent ? (
          <div className="mb-4" aria-live="polite">
            <h2 className="text-xs font-medium text-primary">{inspectedComponent.data.label}</h2>
            <p className="mt-1 text-xs text-cyan">{inspectedComponent.data.implementation}</p>
            <p className="mt-1 text-xs leading-relaxed text-muted">{inspectedComponent.data.description}</p>
            <p className="mt-1 text-[10px] text-muted">Select the component for a full answer in About This System.</p>
          </div>
        ) : (
          <p className="mb-4 text-xs text-muted">Hover or focus a component to see what runs it. Select it to ask more.</p>
        )}
        <h2 className="text-xs font-medium text-primary">Retrieved chunks</h2>
        {retrievedChunks.length === 0 ? (
          <p className="mt-4 text-xs text-muted">No query yet.</p>
        ) : (
          <ol className="mt-3 space-y-2">
            {retrievedChunks.map((chunk) => (
              <li key={chunk.chunk_id} className="min-w-0 text-xs">
                <p className="truncate text-primary">
                  {chunk.url ? (
                    <a
                      href={chunk.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-muted transition-colors hover:text-primary"
                    >
                      {chunk.title}
                    </a>
                  ) : (
                    chunk.title
                  )}
                </p>
                <p className="truncate text-muted">{chunk.source_path} · {chunk.score.toFixed(2)}</p>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  )
}

export default ArchitecturePanel
