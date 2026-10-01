import { getViewportForBounds, Handle, MarkerType, Position, ReactFlow, type Node, type NodeHandle, type NodeProps, type ReactFlowInstance } from '@xyflow/react'
import { useCallback, useEffect, useId, useMemo, useRef, useState, type CSSProperties } from 'react'
import '@xyflow/react/dist/style.css'
import { architectureEdges, architectureNodes, portraitEdges, portraitNodes, type ArchitectureEdge, type NodeId } from '../architecture'
import type { RetrievalChunk } from '../lib/sse'

export type WorkerPod = { name: string; ready: boolean }

type LiveNode = Node<{
  id: NodeId
  label: string
  implementation: string
  active: boolean
  selected: boolean
  cache?: 'hit' | 'miss'
  pods?: WorkerPod[]
  backlog?: number | null
  onPreview: (id: NodeId) => void
  onLeave: () => void
  onInspect: (id: NodeId) => void
}, 'architecture'>

type ArchitecturePanelProps = {
  activeNode?: NodeId | null
  nodeCacheStatus?: Partial<Record<NodeId, 'hit' | 'miss'>>
  retrievedChunks?: RetrievalChunk[]
  selectedNode?: NodeId | null
  answerText?: string | null
  onInspect: (id: NodeId) => void
  workerPods?: WorkerPod[]
  backlog?: number | null
  /** Smallest zoom fitView may pick (mobile: keep labels readable and pan instead of shrinking). */
  fitMinZoom?: number
  /**
   * Phone layout: the two-column portrait graph, and a details panel that is
   * capped in height and can be collapsed so the diagram keeps the room.
   */
  portrait?: boolean
  /** Shown in the portrait details panel when no component is selected. */
  latestAnswer?: string | null
  /** Portrait: leave the diagram for the conversation. */
  onContinueInChat?: () => void
}

// The diagram keeps at least this much height when the details panel is open;
// otherwise the panel may take up to 40% of the region.
const PORTRAIT_DIAGRAM_MIN_PX = 280
const portraitPanelStyle: CSSProperties = {
  maxHeight: `max(5rem, min(40%, calc(100% - ${PORTRAIT_DIAGRAM_MIN_PX}px)))`,
}

function Chevron({ direction }: { direction: 'up' | 'down' }) {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={direction === 'down' ? 'M4 6l4 4 4-4' : 'M4 10l4-4 4 4'} />
    </svg>
  )
}

const handleStyle: CSSProperties = {
  width: 1,
  height: 1,
  border: 0,
  background: 'transparent',
  opacity: 0,
}

const nodeWidth = 124
const nodeHeight = 42
const PORTRAIT_FIT_PADDING = 0.06
const portraitBounds = {
  x: 0,
  y: 0,
  width: Math.max(...portraitNodes.map((node) => node.position.x)) + nodeWidth,
  height: Math.max(...portraitNodes.map((node) => node.position.y)) + nodeHeight,
}
const nodeHandles: NodeHandle[] = [
  { id: 'left', type: 'target', position: Position.Left, x: 0, y: nodeHeight / 2 },
  { id: 'top', type: 'target', position: Position.Top, x: nodeWidth / 2, y: 0 },
  { id: 'right', type: 'source', position: Position.Right, x: nodeWidth, y: nodeHeight / 2 },
  { id: 'bottom', type: 'source', position: Position.Bottom, x: nodeWidth / 2, y: nodeHeight },
]

const flowStyle = {
  '--xy-background-color': 'var(--color-panel)',
  '--xy-edge-stroke': 'var(--color-muted)',
  '--xy-edge-stroke-selected': 'var(--color-cyan)',
  '--xy-attribution-background-color': 'var(--color-panel)',
} as CSSProperties

function ArchitectureNodeView({ data }: NodeProps<LiveNode>) {
  const nodeStyle = [
    data.active ? 'bg-cyan text-canvas' : 'bg-panel text-primary hover:bg-canvas focus-within:bg-canvas',
    data.selected ? 'border-primary ring-1 ring-primary/60' : data.active ? 'border-cyan' : 'border-hairline hover:border-muted focus-within:border-muted',
  ].join(' ')
  return (
    <div
      className={`relative h-[42px] w-[124px] rounded-[3px] border text-center text-xs transition-colors ${nodeStyle}`}
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
        className="nopan nodrag relative flex h-full w-full after:absolute after:-inset-[11px] after:content-[''] cursor-pointer flex-col items-center justify-center px-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan"
      >
        {data.label}
        {data.cache && <span className={`text-[11px] ${data.active ? 'text-canvas/80' : 'text-muted'}`}>{data.cache}</span>}
        {data.pods && (
          <span className="flex items-center gap-1" role="img" aria-label={`${data.pods.length} worker pods`}>
            {data.pods.map((pod) => <span key={pod.name} aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${pod.ready ? 'bg-cyan' : 'bg-muted'}`} />)}
            {typeof data.backlog === 'number' && data.backlog > 0 && <span className="text-[11px] text-muted">{data.backlog}</span>}
          </span>
        )}
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
// Portrait rows sit closer together than the default 20px step offset allows
// for, which made the Vector Search -> MySQL arrow loop back on itself.
const portraitEdgeOptions = { ...defaultEdgeOptions, pathOptions: { offset: 8 } }

function ArchitecturePanel({ activeNode, nodeCacheStatus, retrievedChunks = [], selectedNode, answerText, onInspect, workerPods, backlog, fitMinZoom, portrait = false, latestAnswer, onContinueInChat }: ArchitecturePanelProps) {
  const [hoveredNode, setHoveredNode] = useState<NodeId | null>(null)
  // Portrait only: the details panel starts collapsed (unless a component is
  // already selected) so the diagram gets the room.
  const [detailsOpen, setDetailsOpen] = useState(() => selectedNode != null)
  const detailsId = useId()
  // Collapsing/expanding swaps one button for the other; keep keyboard focus
  // on whichever control is now showing (a node tap leaves focus alone).
  const expandButtonRef = useRef<HTMLButtonElement>(null)
  const collapseButtonRef = useRef<HTMLButtonElement>(null)
  const moveFocusOnToggleRef = useRef(false)
  useEffect(() => {
    if (!moveFocusOnToggleRef.current) return
    moveFocusOnToggleRef.current = false
    ;(detailsOpen ? collapseButtonRef : expandButtonRef).current?.focus()
  }, [detailsOpen])
  function toggleDetails(open: boolean) {
    moveFocusOnToggleRef.current = true
    setDetailsOpen(open)
  }
  const inspectorRef = useRef<HTMLDivElement>(null)
  const inspectRef = useRef(onInspect)
  const portraitStateRef = useRef({ portrait, selectedNode, detailsOpen })
  useEffect(() => {
    inspectRef.current = onInspect
    portraitStateRef.current = { portrait, selectedNode, detailsOpen }
  }, [onInspect, portrait, selectedNode, detailsOpen])
  const previewNode = useCallback((id: NodeId) => setHoveredNode(id), [])
  const leaveNode = useCallback(() => setHoveredNode(null), [])
  const inspectNode = useCallback((id: NodeId) => {
    const current = portraitStateRef.current
    if (current.portrait) {
      // Any tap opens the details. Re-tapping the selected component while
      // they are collapsed only reopens its answer instead of asking again.
      setDetailsOpen(true)
      if (id === current.selectedNode && !current.detailsOpen) return
    }
    inspectRef.current(id)
  }, [])
  const inspectedNode = hoveredNode ?? selectedNode
  const inspectedComponent = architectureNodes.find((node) => node.id === inspectedNode)
  const selectedComponent = architectureNodes.find((node) => node.id === selectedNode)
  useEffect(() => {
    if (inspectorRef.current) inspectorRef.current.scrollTop = 0
  }, [inspectedNode])
  // Portrait: refit whenever the details panel (or the keyboard/focus mode)
  // changes the diagram's height, so all components stay in view.
  const flowRef = useRef<ReactFlowInstance<LiveNode, ArchitectureEdge> | null>(null)
  const flowBoxRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const box = flowBoxRef.current
    if (!portrait || !box) return
    let frame = 0
    const observer = new ResizeObserver(() => {
      // Next frame, after React Flow has recorded the new size. Sets the
      // viewport directly: fitView is deferred by React Flow while node data
      // is changing (as it is when a tap starts a request), so it can miss.
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        if (!box.clientWidth || !box.clientHeight) return
        void flowRef.current?.setViewport(getViewportForBounds(portraitBounds, box.clientWidth, box.clientHeight, fitMinZoom ?? 0.5, 1, PORTRAIT_FIT_PADDING))
      })
    })
    observer.observe(box)
    return () => {
      observer.disconnect()
      cancelAnimationFrame(frame)
    }
  }, [portrait, fitMinZoom])
  const nodes = useMemo<LiveNode[]>(() => (portrait ? portraitNodes : architectureNodes).map((node) => ({
    ...node,
    // Known dimensions and handle positions keep nodes and arrows visible during updates.
    width: nodeWidth,
    height: nodeHeight,
    handles: nodeHandles,
    data: {
      ...node.data,
      id: node.id,
      active: node.id === activeNode,
      selected: node.id === selectedNode,
      cache: nodeCacheStatus?.[node.id],
      pods: node.id === 'worker' ? workerPods : undefined,
      backlog: node.id === 'worker' ? backlog : undefined,
      onPreview: previewNode,
      onLeave: leaveNode,
      onInspect: inspectNode,
    },
  })), [activeNode, inspectNode, leaveNode, nodeCacheStatus, previewNode, selectedNode, workerPods, backlog, portrait])

  // The same details render in the desktop inspector and the portrait panel.
  const details = (
    <>
      {inspectedComponent ? (
        <div className="mb-4" aria-live="polite">
          <h2 className="text-xs font-medium text-primary">{inspectedComponent.data.label}</h2>
          <p className="mt-1 text-xs text-cyan">{inspectedComponent.data.implementation}</p>
          <p className="mt-1 text-xs leading-relaxed text-muted">{inspectedComponent.data.description}</p>
          {answerText !== undefined && inspectedNode === selectedNode ? (
            <div className="mt-3 border-l border-cyan pl-3">
              <h3 className="text-[11px] text-cyan">About This System answer</h3>
              <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-primary">{answerText || 'Working…'}</p>
              {onContinueInChat && answerText && (
                <button
                  type="button"
                  onClick={onContinueInChat}
                  className="-ml-2 mt-1 inline-flex min-h-11 items-center px-2 text-xs text-primary underline underline-offset-4 transition-colors hover:text-cyan"
                >
                  Continue in chat →
                </button>
              )}
            </div>
          ) : (
            <p className="mt-1 text-[11px] text-muted">Select the component for a full answer in About This System.</p>
          )}
        </div>
      ) : (
        <>
          <p className="mb-4 text-xs text-muted">
            {portrait ? 'Tap a component to see what runs it and ask about it.' : 'Hover or focus a component to see what runs it. Select it to ask more.'}
          </p>
          {portrait && latestAnswer && (
            <div className="mb-4">
              <h2 className="text-xs font-medium text-primary">Latest answer</h2>
              <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-relaxed text-primary">{latestAnswer}</p>
            </div>
          )}
        </>
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
    </>
  )

  return (
    <section aria-label="Architecture" className="flex min-h-0 min-w-0 flex-col bg-panel">
      <div ref={flowBoxRef} className="min-h-0 flex-1">
        <ReactFlow
          onInit={(instance) => { flowRef.current = instance }}
          aria-label="System architecture diagram"
          nodes={nodes}
          edges={portrait ? portraitEdges : architectureEdges}
          defaultEdgeOptions={portrait ? portraitEdgeOptions : defaultEdgeOptions}
          nodeTypes={nodeTypes}
          colorMode="dark"
          style={flowStyle}
          fitView
          fitViewOptions={{ padding: portrait ? PORTRAIT_FIT_PADDING : 0.12, maxZoom: 1, ...(fitMinZoom ? { minZoom: fitMinZoom } : {}) }}
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
      {!portrait ? (
        <div ref={inspectorRef} className="h-44 shrink-0 overflow-y-auto border-t border-hairline px-4 py-4 md:px-7">
          {details}
        </div>
      ) : detailsOpen ? (
        // Capped so the diagram keeps its room; the collapse button stays
        // pinned while the details scroll, and text keeps clear of it.
        <div id={detailsId} className="relative flex shrink-0 flex-col border-t border-hairline" style={portraitPanelStyle}>
          <div ref={inspectorRef} className="min-h-0 flex-1 overflow-y-auto py-4 pl-4 pr-14">
            {details}
          </div>
          <button
            ref={collapseButtonRef}
            type="button"
            aria-label="Collapse details"
            aria-expanded="true"
            aria-controls={detailsId}
            onClick={() => toggleDetails(false)}
            className="absolute right-1 top-1 flex size-11 items-center justify-center rounded-[3px] text-muted transition-colors hover:text-primary focus-visible:outline-1 focus-visible:outline-cyan"
          >
            <Chevron direction="down" />
          </button>
        </div>
      ) : (
        <button
          ref={expandButtonRef}
          type="button"
          aria-expanded="false"
          aria-controls={detailsId}
          onClick={() => toggleDetails(true)}
          className="flex min-h-11 w-full shrink-0 items-center justify-between gap-3 border-t border-hairline px-4 text-left text-xs text-muted transition-colors hover:text-primary"
        >
          <span className="min-w-0 truncate">{selectedComponent ? `${selectedComponent.data.label} details` : 'Details'}</span>
          <span className="flex shrink-0 items-center gap-2 tabular-nums">
            {retrievedChunks.length} {retrievedChunks.length === 1 ? 'chunk' : 'chunks'}
            <Chevron direction="up" />
          </span>
        </button>
      )}
    </section>
  )
}

export default ArchitecturePanel
