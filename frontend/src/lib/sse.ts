import type { NodeId } from '../architecture'
import type { ApiCorpus, HistoryTurn } from './conversation'
import { createIdleWatchdog } from './idleWatchdog'

// The server pings at least every 15 s (DESIGN-002 §7.4), so 45 s without a
// single byte means the connection is dead even if the socket never closed.
export const STREAM_IDLE_TIMEOUT_MS = 45_000
export const CONNECTION_LOST_MESSAGE = 'Connection lost — try again.'

export type StageEvent = {
  request_id: string
  seq: number
  node: NodeId
  status: 'start' | 'end'
  t_ms: number
  duration_ms?: number
  cache?: 'hit' | 'miss'
  meta?: Record<string, string | number>
}

export type RetrievalChunk = {
  n: number
  chunk_id: number
  source_path: string
  title: string
  score: number
  snippet?: string
  start_line?: number
  end_line?: number
  url?: string
}

// `rewritten_query` is present when a follow-up was rewritten for retrieval.
export type RetrievalEvent = { chunks: RetrievalChunk[]; rewritten_query?: string }
export type TokenEvent = { text: string }
export type DoneEvent = {
  total_ms: number
  // `stopped` is part of the §9.2 contract; the server logs it but the client
  // that stopped is gone, so in practice the browser never receives it.
  mode: 'full' | 'retrieval_only' | 'stopped'
  answer_cache: 'hit' | 'miss'
  tokens_in?: number
  tokens_out?: number
}
export type ErrorEvent = {
  code: 'rate_limited' | 'budget_exhausted' | 'internal'
  message: string
  retry_after_s?: number
}

export type AskCallbacks = {
  onStage?: (event: StageEvent) => void
  onRetrieval?: (event: RetrievalEvent) => void
  onToken?: (event: TokenEvent) => void
  onDone?: (event: DoneEvent) => void
  onError?: (event: ErrorEvent) => void
}

export async function askQuestion(
  question: string,
  corpus: ApiCorpus,
  callbacks: AskCallbacks,
  signal?: AbortSignal,
  history: HistoryTurn[] = [],
  idleTimeoutMs: number = STREAM_IDLE_TIMEOUT_MS,
): Promise<void> {
  if (signal?.aborted) return
  // One controller for the fetch: aborted by the caller (Stop, unmount) or by
  // the idle watchdog when the stream stalls. The watchdog also covers the wait
  // for response headers.
  const controller = new AbortController()
  const forwardAbort = () => controller.abort()
  signal?.addEventListener('abort', forwardAbort, { once: true })
  let activeReader: ReadableStreamDefaultReader<Uint8Array> | null = null
  const watchdog = createIdleWatchdog(idleTimeoutMs, () => {
    controller.abort()
    // Also cancel the reader directly, so a pending read settles even if the
    // body stream does not error on abort.
    activeReader?.cancel().catch(() => { /* Already closed. */ })
  })
  try {
    const response = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // `history` is omitted for a first question so the server skips the rewrite.
      body: JSON.stringify(history.length > 0 ? { question, corpus, history } : { question, corpus }),
      signal: controller.signal,
    })

    if (!response.ok) {
      throw new Error(`Request failed (${response.status})`)
    }
    if (!response.body) {
      throw new Error('The response had no stream')
    }

    const reader = response.body.getReader()
    activeReader = reader
    const decoder = new TextDecoder()
    let buffer = ''
    let completed = false

    // A read can end in the middle of a UTF-8 character or an SSE frame.
    // TextDecoder retains partial characters; buffer retains partial frames.
    function dispatchFrames() {
      buffer = buffer.replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const eventType = frame.split('\n').find((line) => line.startsWith('event:'))?.slice(6).trim()
        const data = frame.split('\n').filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart()).join('\n')

        if (eventType && data) {
          let payload: unknown
          try {
            payload = JSON.parse(data)
          } catch {
            callbacks.onError?.({ code: 'internal', message: 'Something went wrong — try again.' })
            completed = true
            return
          }
          switch (eventType) {
            case 'stage': callbacks.onStage?.(payload as StageEvent); break
            case 'retrieval': callbacks.onRetrieval?.(payload as RetrievalEvent); break
            case 'token': callbacks.onToken?.(payload as TokenEvent); break
            case 'done': callbacks.onDone?.(payload as DoneEvent); completed = true; break
            case 'error': callbacks.onError?.(payload as ErrorEvent); completed = true; break
          }
        }
        if (completed) return
        boundary = buffer.indexOf('\n\n')
      }
    }

    try {
      while (!completed) {
        const { done, value } = await reader.read()
        // Any bytes count as life, including `: ping` heartbeats (which the
        // frame parser skips because they carry no `event:` line).
        if (value && value.byteLength > 0) watchdog.reset()
        if (done) {
          buffer += decoder.decode()
          dispatchFrames()
          if (!completed) throw new Error('The response ended before completion')
          break
        }
        buffer += decoder.decode(value, { stream: true })
        dispatchFrames()
      }
    } finally {
      if (completed) {
        // The terminal event is authoritative; a cancelled reader must not
        // turn a successful answer into a second, spurious error callback.
        try { await reader.cancel() } catch { /* The stream may already be closed. */ }
      }
      reader.releaseLock()
    }
  } catch (error) {
    if (watchdog.fired) {
      callbacks.onError?.({ code: 'internal', message: CONNECTION_LOST_MESSAGE })
      return
    }
    if (signal?.aborted) return
    callbacks.onError?.({
      code: 'internal',
      // fetch() and reader.read() reject with a TypeError when the network drops.
      message: error instanceof TypeError ? CONNECTION_LOST_MESSAGE
        : error instanceof Error ? error.message : 'Something went wrong — try again.',
    })
  } finally {
    watchdog.stop()
    signal?.removeEventListener('abort', forwardAbort)
  }
}
