export type ClusterPodEvent = {
  type: 'ADDED' | 'MODIFIED' | 'DELETED'
  pod: string
  phase: string
  ready: boolean
}

export type ClusterBacklogEvent = { backlog: number }

export type ClusterStreamCallbacks = {
  onPod?: (event: ClusterPodEvent) => void
  onBacklog?: (event: ClusterBacklogEvent) => void
  /** The backend has no in-cluster Kubernetes API access (e.g. local dev). */
  onUnavailable?: (message: string) => void
}

/**
 * GET-only SSE (unlike /api/ask, which needs a POST body and so uses a
 * manual fetch reader — see lib/sse.ts), so the native EventSource works
 * here and gets its automatic reconnect-on-drop behavior for free.
 */
export function connectClusterStream(callbacks: ClusterStreamCallbacks): () => void {
  const source = new EventSource('/api/cluster/stream')

  source.addEventListener('pod', (event) => {
    try {
      callbacks.onPod?.(JSON.parse((event as MessageEvent).data))
    } catch {
      // Ignore a malformed frame; the stream keeps going.
    }
  })
  source.addEventListener('backlog', (event) => {
    try {
      callbacks.onBacklog?.(JSON.parse((event as MessageEvent).data))
    } catch {
      // Ignore a malformed frame; the stream keeps going.
    }
  })
  source.addEventListener('cluster_unavailable', (event) => {
    let message = 'Cluster view unavailable.'
    try {
      message = JSON.parse((event as MessageEvent).data).message ?? message
    } catch {
      // Fall back to the default message above.
    }
    callbacks.onUnavailable?.(message)
    source.close()
  })

  return () => source.close()
}
