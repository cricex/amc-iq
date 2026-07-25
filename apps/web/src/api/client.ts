import type { AskResult, AskRequest, FabricStatus, StreamEvent } from '../types'

// In production the UI calls the Container App API directly (VITE_API_BASE_URL) so that Server-Sent
// Events stream live; the Static Web App managed proxy buffers SSE, which would leave the IQ layers
// stuck "queued". Locally VITE_API_BASE_URL is unset and the Vite dev proxy handles /api.
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const ASK_ENDPOINT = `${API_BASE}/api/ask`
const STREAM_ENDPOINT = `${API_BASE}/api/ask/stream`
const FABRIC_STATUS_ENDPOINT = `${API_BASE}/api/fabric/status`

/** Resolve a relative backend link (e.g. /api/evidence/doc?...) against the API base so evidence
 *  links work when the UI is served from the Static Web App but the API is the Container App. */
export function resolveApiUrl(url: string | undefined | null): string | undefined {
  if (!url) return undefined
  return url.startsWith('/api/') ? `${API_BASE}${url}` : url
}

/** Error carrying the backend HTTP status + detail message (e.g. "Name a patient in the question").
 *  A 4xx means the question was invalid (bad/missing patient or trial), not a connectivity failure. */
export class RequestError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'RequestError'
    this.status = status
  }
}

export class StreamError extends Error {
  receivedProgress: boolean
  constructor(message: string, receivedProgress: boolean) {
    super(message)
    this.name = 'StreamError'
    this.receivedProgress = receivedProgress
  }
}

export type StreamFailureDecision = 'validation' | 'terminal-composition' | 'non-streaming-fallback'

export function decideStreamFailure(error: unknown): StreamFailureDecision {
  if (!(error instanceof StreamError)) return 'non-streaming-fallback'
  return error.receivedProgress ? 'terminal-composition' : 'validation'
}

export function shouldUseSampleFallback(error: unknown): boolean {
  return !(error instanceof RequestError)
}

export function authorizationHeaders(accessToken: string | null): Record<string, string> {
  if (accessToken === null) return {}
  if (!accessToken.trim()) throw new Error('Access token is required')
  return { Authorization: `Bearer ${accessToken}` }
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string }
    if (body && typeof body.detail === 'string') return body.detail
  } catch {
    // non-JSON error body
  }
  return `Request failed with ${response.status}`
}

/** Read-only Fabric capacity state (Active/Paused/Resuming/Pausing/Unknown) so the UI can tell users
 *  whether a live assessment will work. The backend never errors this route; on any backend/network
 *  failure we return an "Unknown" status so the chip stays informative rather than throwing. */
export async function getFabricStatus(): Promise<FabricStatus> {
  const unknown: FabricStatus = { state: 'Unknown', capacityName: '', portalUrl: '' }
  try {
    const response = await fetch(FABRIC_STATUS_ENDPOINT, { headers: { Accept: 'application/json' } })
    if (!response.ok) return unknown
    return (await response.json()) as FabricStatus
  } catch {
    return unknown
  }
}

export async function ask(
  question: string,
  accessToken: string | null,
  patientId?: string,
  trialId?: string,
): Promise<AskResult> {
  const response = await fetch(ASK_ENDPOINT, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authorizationHeaders(accessToken),
    },
    body: JSON.stringify(buildRequest(question, patientId, trialId)),
  })

  if (!response.ok) {
    throw new RequestError(await errorDetail(response), response.status)
  }

  return (await response.json()) as AskResult
}

export async function askStream(
  question: string,
  onEvent: (event: StreamEvent) => void,
  accessToken: string | null,
  patientId?: string,
  trialId?: string,
): Promise<void> {
  const response = await fetch(STREAM_ENDPOINT, {
    method: 'POST',
    headers: {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
      ...authorizationHeaders(accessToken),
    },
    body: JSON.stringify(buildRequest(question, patientId, trialId)),
  })

  if (!response.ok) {
    throw new RequestError(await errorDetail(response), response.status)
  }
  if (!response.body) {
    throw new Error(`Stream request failed with ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let receivedProgress = false
  const handleEvent = (event: StreamEvent) => {
    if (event.type === 'error') {
      throw new StreamError(event.data.message, receivedProgress)
    }
    receivedProgress = true
    onEvent(event)
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = frames.pop() ?? ''

    for (const frame of frames) {
      emitFrame(frame, handleEvent)
    }
  }

  buffer += decoder.decode()
  if (buffer.trim()) {
    emitFrame(buffer, handleEvent)
  }
}

function buildRequest(question: string, patientId?: string, trialId?: string): AskRequest {
  return {
    question,
    ...(patientId ? { patientId } : {}),
    ...(trialId ? { trialId } : {}),
  }
}

function emitFrame(frame: string, onEvent: (event: StreamEvent) => void) {
  const lines = frame.split(/\r?\n/)
  let eventType = 'message'
  const dataLines: string[] = []

  for (const line of lines) {
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim()
    }

    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  if (!dataLines.length) return

  const data = JSON.parse(dataLines.join('\n')) as unknown

  switch (eventType) {
    case 'plan':
      onEvent({ type: 'plan', data: data as StreamEventOf<'plan'> })
      break
    case 'source_query':
      onEvent({ type: 'source_query', data: data as StreamEventOf<'source_query'> })
      break
    case 'source_result':
      onEvent({ type: 'source_result', data: data as StreamEventOf<'source_result'> })
      break
    case 'token':
      onEvent({ type: 'token', data: data as StreamEventOf<'token'> })
      break
    case 'agent_activity':
      onEvent({ type: 'agent_activity', data: data as StreamEventOf<'agent_activity'> })
      break
    case 'final':
      onEvent({ type: 'final', data: data as StreamEventOf<'final'> })
      break
    case 'error':
      onEvent({ type: 'error', data: data as StreamEventOf<'error'> })
      break
    default:
      break
  }
}

type StreamEventOf<T extends StreamEvent['type']> = Extract<StreamEvent, { type: T }>['data']
