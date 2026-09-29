import type { Edge, Node } from '@xyflow/react'

export type NodeId =
  | 'edge' | 'api' | 'answer_cache' | 'queue' | 'worker'
  | 'embed_cache' | 'embed' | 'vector_search' | 'mysql' | 'llm'

export type ArchitectureNode = Node<{ label: string }, 'architecture'> & { id: NodeId }
export type ArchitectureEdge = Edge & { source: NodeId; target: NodeId }

export const architectureNodes: ArchitectureNode[] = [
  { id: 'edge', type: 'architecture', data: { label: 'Edge' }, position: { x: 0, y: 0 } },
  { id: 'api', type: 'architecture', data: { label: 'API' }, position: { x: 180, y: 0 } },
  { id: 'answer_cache', type: 'architecture', data: { label: 'Answer Cache' }, position: { x: 360, y: 0 } },
  { id: 'queue', type: 'architecture', data: { label: 'Queue' }, position: { x: 360, y: 120 } },
  { id: 'worker', type: 'architecture', data: { label: 'Worker' }, position: { x: 540, y: 120 } },
  { id: 'embed_cache', type: 'architecture', data: { label: 'Embed Cache' }, position: { x: 180, y: 260 } },
  { id: 'embed', type: 'architecture', data: { label: 'Embed' }, position: { x: 360, y: 260 } },
  { id: 'vector_search', type: 'architecture', data: { label: 'Vector Search' }, position: { x: 540, y: 260 } },
  { id: 'mysql', type: 'architecture', data: { label: 'MySQL' }, position: { x: 720, y: 260 } },
  { id: 'llm', type: 'architecture', data: { label: 'LLM' }, position: { x: 540, y: 380 } },
]

export const architectureEdges: ArchitectureEdge[] = [
  { id: 'edge-api', source: 'edge', target: 'api', sourceHandle: 'right', targetHandle: 'left' },
  { id: 'api-answer_cache', source: 'api', target: 'answer_cache', sourceHandle: 'right', targetHandle: 'left' },
  { id: 'answer_cache-queue', source: 'answer_cache', target: 'queue', sourceHandle: 'bottom', targetHandle: 'top' },
  { id: 'queue-worker', source: 'queue', target: 'worker', sourceHandle: 'right', targetHandle: 'left' },
  { id: 'worker-vector_search', source: 'worker', target: 'vector_search', sourceHandle: 'bottom', targetHandle: 'top' },
  { id: 'embed_cache-embed', source: 'embed_cache', target: 'embed', sourceHandle: 'right', targetHandle: 'left' },
  { id: 'embed-vector_search', source: 'embed', target: 'vector_search', sourceHandle: 'right', targetHandle: 'left' },
  { id: 'vector_search-mysql', source: 'vector_search', target: 'mysql', sourceHandle: 'right', targetHandle: 'left' },
  { id: 'vector_search-llm', source: 'vector_search', target: 'llm', sourceHandle: 'bottom', targetHandle: 'top' },
]
