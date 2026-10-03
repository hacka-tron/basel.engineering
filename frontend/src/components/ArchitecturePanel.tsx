import { getViewportForBounds, Handle, MarkerType, Position, ReactFlow, type Node, type NodeHandle, type NodeProps, type ReactFlowInstance } from '@xyflow/react'
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import '@xyflow/react/dist/style.css'
import { architectureEdges, architectureNodes, portraitEdges, portraitNodes, type ArchitectureEdge, type NodeId } from '../architecture'
import { deselectsOnKey, PORTRAIT_DETAILS_HINT } from '../lib/detailsPanel'
import { boundsOf, createRefitter } from '../lib/diagramFit'
import { panIntoStrip } from '../lib/detailsSheet'
import type { RetrievalChunk } from '../lib/sse'
import { ContinueInChat, DetailsSheet, LockedBar, SheetSection, SHEET_BODY, SHEET_TITLE } from './DetailsSheet'

export type WorkerPod = { name: string; ready: boolean }

type LiveNode = Node<{
  id: NodeId
  label: string
  implementation: string
  active: boolean
  selected: boolean
  /** False while the phone details sheet covers the diagram: nodes leave the Tab order so keyboard focus goes to the sheet. */
  tabbable: boolean
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
  /**
   * Clears the selection: a click or tap on empty diagram space, Escape, and
   * on phones the details panel's close chevron. An answer still streaming
   * for the component keeps streaming into Chat; only the selection clears.
   */
  onDeselect?: () => void
  workerPods?: WorkerPod[]
  backlog?: number | null
  /** Smallest zoom fitView may pick (mobile: keep labels readable and pan instead of shrinking). */
  fitMinZoom?: number
  /**
   * Phone layout: the two-column portrait graph, and a pull-up details sheet (80% of the region) open only while a component is selected.
   */
  portrait?: boolean
  /** Portrait: leave the diagram for the conversation. */
  onContinueInChat?: () => void
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
const DESKTOP_FIT_PADDING = 0.12
const portraitBounds = boundsOf(portraitNodes.map((node) => node.position), nodeWidth, nodeHeight)
const desktopBounds = boundsOf(architectureNodes.map((node) => node.position), nodeWidth, nodeHeight)
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
        tabIndex={data.tabbable ? 0 : -1}
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

function ArchitecturePanel({ activeNode, nodeCacheStatus, retrievedChunks = [], selectedNode, answerText, onInspect, onDeselect, workerPods, backlog, fitMinZoom, portrait = false, onContinueInChat }: ArchitecturePanelProps) {
  const [hoveredNode, setHoveredNode] = useState<NodeId | null>(null)
  // Portrait: the details panel is open exactly while a component is
  // selected; otherwise the bar under the diagram is locked (lib/detailsPanel.ts).
  const detailsOpen = selectedNode != null
  const detailsRef = useRef<HTMLDivElement>(null)
  const lockedBarRef = useRef<HTMLButtonElement>(null)
  // Deselecting unmounts the open panel. If it held keyboard focus (or its
  // close chevron was used), focus moves to the locked bar that replaces it
  // instead of falling to <body>. A tap on empty diagram space leaves focus alone.
  const focusLockedBarRef = useRef(false)
  const deselect = useCallback((fromPanel: boolean) => {
    if (portrait && (fromPanel || detailsRef.current?.contains(document.activeElement))) focusLockedBarRef.current = true
    // Drop a lingering focus/hover preview too, so the desktop inspector
    // returns to its empty state.
    setHoveredNode(null)
    onDeselect?.()
  }, [onDeselect, portrait])
  useEffect(() => {
    if (selectedNode != null || !focusLockedBarRef.current) return
    focusLockedBarRef.current = false
    lockedBarRef.current?.focus()
  }, [selectedNode])
  // Escape deselects first. Capture phase, so it runs before the phone Diagram
  // view's Escape-returns-to-Chat listener, which skips handled events: the
  // first Escape deselects, the next one returns to Chat.
  const hasSelection = selectedNode != null && onDeselect != null
  useEffect(() => {
    if (!hasSelection) return
    function handleKeyDown(event: KeyboardEvent) {
      if (!deselectsOnKey(event, true)) return
      event.preventDefault()
      deselect(false)
    }
    document.addEventListener('keydown', handleKeyDown, true)
    return () => document.removeEventListener('keydown', handleKeyDown, true)
  }, [hasSelection, deselect])
  const inspectorRef = useRef<HTMLDivElement>(null)
  // Stable for the node data, so a parent re-render (every streamed token)
  // does not rebuild the nodes.
  const inspectRef = useRef(onInspect)
  useEffect(() => {
    inspectRef.current = onInspect
  }, [onInspect])
  const inspectNode = useCallback((id: NodeId) => inspectRef.current(id), [])
  const previewNode = useCallback((id: NodeId) => setHoveredNode(id), [])
  const leaveNode = useCallback(() => setHoveredNode(null), [])
  const inspectedNode = hoveredNode ?? selectedNode
  const inspectedComponent = architectureNodes.find((node) => node.id === inspectedNode)
  const selectedComponent = architectureNodes.find((node) => node.id === selectedNode)
  useEffect(() => {
    if (inspectorRef.current) inspectorRef.current.scrollTop = 0
  }, [inspectedNode])
  // Refit whenever the container changes size: a window resize, the portrait
  // <-> desktop switch, the details panel or keyboard/focus mode changing the
  // diagram's height. fitView alone only runs once, so without this a zoom
  // chosen while the window was narrow stuck after widening it again.
  const flowRef = useRef<ReactFlowInstance<LiveNode, ArchitectureEdge> | null>(null)
  const flowBoxRef = useRef<HTMLDivElement>(null)
  // Phones: the sheet covers the bottom 80%, so the selected node is panned
  // into the strip left above it (same zoom as the fit); deselecting returns
  // to the plain fit. Desktop always uses the plain fit.
  const selectedRef = useRef(selectedNode)
  useEffect(() => { selectedRef.current = selectedNode })
  const viewportFor = useCallback((width: number, height: number) => {
    const fit = getViewportForBounds(portrait ? portraitBounds : desktopBounds, width, height, fitMinZoom ?? 0.5, 1, portrait ? PORTRAIT_FIT_PADDING : DESKTOP_FIT_PADDING)
    const node = portrait ? portraitNodes.find((candidate) => candidate.id === selectedRef.current) : undefined
    // The flow box starts at the region's top, so its y is the region's y.
    const region = flowBoxRef.current?.parentElement
    if (!node || !region) return fit
    return panIntoStrip(fit, node.position.y + nodeHeight / 2, region.clientHeight)
  }, [portrait, fitMinZoom])
  useEffect(() => {
    const box = flowBoxRef.current
    if (!portrait || !box || !flowRef.current) return
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    void flowRef.current.setViewport(viewportFor(box.clientWidth, box.clientHeight), { duration: reduceMotion ? 0 : 260 })
  }, [portrait, selectedNode, viewportFor])
  useEffect(() => {
    const box = flowBoxRef.current
    if (!box) return
    // Next frame, after React Flow has recorded the new size. Sets the
    // viewport directly: fitView is deferred by React Flow while node data
    // is changing (as it is when a tap starts a request), so it can miss.
    // Trace updates do not resize the box, so they never trigger a refit.
    const refitter = createRefitter(
      () => ({ width: box.clientWidth, height: box.clientHeight }),
      ({ width, height }) => {
        void flowRef.current?.setViewport(viewportFor(width, height))
      },
      { request: (callback) => requestAnimationFrame(callback), cancel: (handle) => cancelAnimationFrame(handle) },
    )
    const observer = new ResizeObserver(refitter.request)
    observer.observe(box)
    return () => {
      observer.disconnect()
      refitter.cancel()
    }
  }, [viewportFor])
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
      tabbable: !(portrait && selectedNode != null),
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
        // Desktop only: the portrait panel opens only with a component selected.
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
              <p className="truncate text-muted">{chunk.source_path === chunk.title ? '' : `${chunk.source_path} · `}{chunk.score.toFixed(2)}</p>
            </li>
          ))}
        </ol>
      )}
    </>
  )

  const chunkList = retrievedChunks.length === 0 ? (
    <p className="text-[13px] text-muted">No query yet.</p>
  ) : (
    <ol className="space-y-3">
      {retrievedChunks.map((chunk) => (
        <li key={chunk.chunk_id} className="min-w-0 text-[13px] leading-[1.5]">
          <p className="break-words text-primary">
            {chunk.url ? (
              <a href={chunk.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-4 transition-colors hover:text-cyan">{chunk.title}</a>
            ) : chunk.title}
          </p>
          <p className="break-words text-muted">{chunk.source_path === chunk.title ? '' : `${chunk.source_path} · `}{chunk.score.toFixed(2)}</p>
        </li>
      ))}
    </ol>
  )

  return (
    <section aria-label="Architecture" className={`flex min-h-0 min-w-0 flex-col bg-panel ${portrait ? 'relative overflow-hidden' : ''}`}>
      <div ref={flowBoxRef} className="min-h-0 flex-1">
        <ReactFlow
          onInit={(instance) => {
            flowRef.current = instance
            // A component already selected when the diagram mounts (say, after Portfolio -> Diagram).
            const box = flowBoxRef.current
            if (portrait && selectedRef.current && box) void instance.setViewport(viewportFor(box.clientWidth, box.clientHeight))
          }}
          aria-label="System architecture diagram"
          // Phones, sheet open (owner, 2026-10-02): nodes and arrows ignore taps, so any
          // tap in the strip falls through to the pane and closes the sheet (index.css).
          className={portrait && detailsOpen ? 'sheet-open' : undefined}
          nodesFocusable={!(portrait && detailsOpen)}
          edgesFocusable={!(portrait && detailsOpen)}
          nodes={nodes}
          edges={portrait ? portraitEdges : architectureEdges}
          defaultEdgeOptions={portrait ? portraitEdgeOptions : defaultEdgeOptions}
          nodeTypes={nodeTypes}
          colorMode="dark"
          style={flowStyle}
          fitView
          fitViewOptions={{ padding: portrait ? PORTRAIT_FIT_PADDING : DESKTOP_FIT_PADDING, maxZoom: 1, ...(fitMinZoom ? { minZoom: fitMinZoom } : {}) }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          onNodeMouseEnter={(_event, node) => previewNode(node.id as NodeId)}
          onNodeMouseLeave={leaveNode}
          // Empty space only: React Flow ignores clicks on nodes and edges
          // here, and a drag that pans the diagram is not a click.
          onPaneClick={hasSelection ? () => deselect(false) : undefined}
          zoomOnScroll={false}
          zoomOnDoubleClick={false}
          proOptions={{ hideAttribution: true }}
        />
      </div>
      {!portrait ? (
        <div ref={inspectorRef} className="h-44 shrink-0 overflow-y-auto border-t border-hairline px-4 py-4 md:px-7">
          {details}
        </div>
      ) : (
        <>
          <LockedBar hint={PORTRAIT_DETAILS_HINT} barRef={lockedBarRef} covered={detailsOpen} />
          {selectedComponent && (
            <DetailsSheet
              label={`${selectedComponent.data.label} details`}
              closeLabel={`Close ${selectedComponent.data.label} details and deselect it`}
              onClose={() => deselect(true)}
              sheetRef={detailsRef}
              scrollRef={inspectorRef}
            >
              <header>
                <h2 className={SHEET_TITLE}>{selectedComponent.data.label}</h2>
                <p className={`mt-2 ${SHEET_BODY} text-cyan`}>{selectedComponent.data.implementation}</p>
              </header>
              <SheetSection heading="What it does">
                <p className={`${SHEET_BODY} break-words text-primary/90`}>{selectedComponent.data.description}</p>
              </SheetSection>
              <SheetSection heading="About This System answer">
                <div className="max-w-[70ch] rounded-[3px] border border-hairline bg-canvas px-4 py-3">
                  <p aria-live="polite" className={`whitespace-pre-wrap break-words ${SHEET_BODY} text-primary`}>{answerText || 'Working…'}</p>
                </div>
                {onContinueInChat && answerText && <ContinueInChat onClick={onContinueInChat} />}
              </SheetSection>
              <SheetSection heading="Retrieved chunks">{chunkList}</SheetSection>
            </DetailsSheet>
          )}
        </>
      )}
    </section>
  )
}

export default ArchitecturePanel
