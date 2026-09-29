import { Handle, MarkerType, Position, ReactFlow, type Node, type NodeProps } from '@xyflow/react'
import { useMemo, type CSSProperties } from 'react'
import '@xyflow/react/dist/style.css'
import { architectureEdges, architectureNodes, type NodeId } from '../architecture'
import type { RetrievalChunk } from '../lib/sse'

type LiveNode = Node<{ label: string; active: boolean; cache?: 'hit' | 'miss' }, 'architecture'>

type ArchitecturePanelProps = {
  activeNode?: NodeId | null
  nodeCacheStatus?: Partial<Record<NodeId, 'hit' | 'miss'>>
  retrievedChunks?: RetrievalChunk[]
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
    <div className={`flex h-[42px] w-[124px] flex-col items-center justify-center rounded-[3px] border bg-panel px-2 text-center text-xs ${data.active ? 'border-cyan text-cyan' : 'border-hairline text-primary'}`}>
      <Handle id="left" type="target" position={Position.Left} style={handleStyle} />
      <Handle id="top" type="target" position={Position.Top} style={handleStyle} />
      {data.label}
      {data.cache && <span className="text-[10px] text-muted">{data.cache}</span>}
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

function ArchitecturePanel({ activeNode, nodeCacheStatus, retrievedChunks = [] }: ArchitecturePanelProps) {
  const nodes = useMemo<LiveNode[]>(() => architectureNodes.map((node) => ({
    ...node,
    data: { ...node.data, active: node.id === activeNode, cache: nodeCacheStatus?.[node.id] },
  })), [activeNode, nodeCacheStatus])

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
          zoomOnScroll={false}
          zoomOnDoubleClick={false}
          proOptions={{ hideAttribution: true }}
        />
      </div>
      <div className="h-36 shrink-0 overflow-y-auto border-t border-hairline px-7 py-5">
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
