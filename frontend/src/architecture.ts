import type { Edge, Node } from '@xyflow/react'

export type NodeId =
  | 'edge' | 'api' | 'answer_cache' | 'queue' | 'worker'
  | 'embed_cache' | 'embed' | 'vector_search' | 'mysql' | 'llm'

export type ArchitectureNode = Node<{
  label: string
  implementation: string
  description: string
}, 'architecture'> & { id: NodeId }
export type ArchitectureEdge = Edge & { source: NodeId; target: NodeId }

// Visitor-facing implementation details. Keep these in sync with the live
// provider configuration and Kubernetes manifests when those change.
export const architectureNodes: ArchitectureNode[] = [
  { id: 'edge', type: 'architecture', data: { label: 'Edge', implementation: 'Cloudflare + Traefik', description: 'Cloudflare receives public traffic; Traefik routes it to the site on k3s.' }, position: { x: 0, y: 0 } },
  { id: 'api', type: 'architecture', data: { label: 'API', implementation: 'Python + FastAPI', description: 'Accepts questions, coordinates the request, and streams progress and answers to the browser.' }, position: { x: 180, y: 0 } },
  { id: 'answer_cache', type: 'architecture', data: { label: 'Answer Cache', implementation: 'Redis Stack semantic cache', description: 'Reuses a grounded answer when a similar question has already been answered.' }, position: { x: 360, y: 0 } },
  { id: 'queue', type: 'architecture', data: { label: 'Queue', implementation: 'Redis Streams', description: 'Passes retrieval jobs from the API to the worker.' }, position: { x: 360, y: 120 } },
  { id: 'worker', type: 'architecture', data: { label: 'Worker', implementation: 'Python retrieval worker on k3s', description: 'Finds matching document chunks and sends trace events back to the API.' }, position: { x: 540, y: 120 } },
  { id: 'embed_cache', type: 'architecture', data: { label: 'Embed Cache', implementation: 'Redis key-value cache', description: 'Avoids regenerating embeddings for questions it has already seen.' }, position: { x: 180, y: 260 } },
  { id: 'embed', type: 'architecture', data: { label: 'Embed', implementation: 'Amazon Titan Text Embeddings V2', description: 'Turns text into vectors so related questions and documents can be compared.' }, position: { x: 360, y: 260 } },
  { id: 'vector_search', type: 'architecture', data: { label: 'Vector Search', implementation: 'Redis Stack / RediSearch', description: 'Uses a vector index to find the closest matching document chunks.' }, position: { x: 540, y: 260 } },
  { id: 'mysql', type: 'architecture', data: { label: 'MySQL', implementation: 'MySQL 8 on k3s', description: 'Stores the source documents, chunks, and query records.' }, position: { x: 720, y: 260 } },
  { id: 'llm', type: 'architecture', data: { label: 'LLM', implementation: 'Amazon Nova Lite via Bedrock', description: 'Writes an answer using the retrieved passages and streams it to the page.' }, position: { x: 540, y: 380 } },
]

export function questionForComponent(id: NodeId): string {
  const component = architectureNodes.find((node) => node.id === id)!
  return `How does the ${component.data.label} component (${component.data.implementation}) work in the current Glassbox system? Explain its role in a request and cite the relevant sources.`
}

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
