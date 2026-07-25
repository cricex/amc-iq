import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ask,
  askStream,
  authorizationHeaders,
  decideStreamFailure,
  RequestError,
  shouldUseSampleFallback,
  StreamError,
} from './client'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('authorizationHeaders', () => {
  it('injects the delegated access token into direct API requests', () => {
    expect(authorizationHeaders('delegated-token')).toEqual({
      Authorization: 'Bearer delegated-token',
    })

  })

  it('rejects an empty access token', () => {
    expect(() => authorizationHeaders('  ')).toThrow('Access token is required')
  })

  it('omits authorization only for the explicit anonymous mock token state', () => {
    expect(authorizationHeaders(null)).toEqual({})
  })

  it('adds the authorization header to the assessment request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ mode: 'mock' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await ask('Is PT-1042 eligible for NCT99004324?', 'delegated-token')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/ask',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer delegated-token',
        }),
      }),
    )
  })

  it('sends an anonymous mock assessment without an authorization header', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ mode: 'mock' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await ask('Is PT-1042 eligible for NCT99004324?', null)

    const request = fetchMock.mock.calls[0][1] as RequestInit
    expect(request.headers).toEqual({
      'Content-Type': 'application/json',
    })
  })

  it('sends the active case context so a free-form question does not need boilerplate IDs', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ mode: 'mock' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await ask(
      'Who owns the next step?',
      'delegated-token',
      'PT-1042',
      'NCT99004324',
    )

    const request = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(request.body))).toEqual({
      question: 'Who owns the next step?',
      patientId: 'PT-1042',
      trialId: 'NCT99004324',
    })
  })
})

describe('shouldUseSampleFallback', () => {
  it('does not mask backend 5xx responses with sample data', () => {
    expect(shouldUseSampleFallback(new RequestError('Service unavailable', 503))).toBe(false)
  })

  it('allows sample data only for transport failures', () => {
    expect(shouldUseSampleFallback(new TypeError('Failed to fetch'))).toBe(true)
  })
})

describe('decideStreamFailure', () => {
  it('returns validation for an explicit backend error before stream progress', () => {
    expect(decideStreamFailure(new StreamError('Name a patient in the question.', false))).toBe('validation')
  })

  it('makes an explicit backend error after progress terminal instead of retrying non-streaming', () => {
    expect(decideStreamFailure(new StreamError('Fabric eligibility evaluation failed', true))).toBe(
      'terminal-composition',
    )
  })

  it.each([
    new TypeError('Failed to fetch'),
    new Error('Stream closed before final result'),
  ])('allows the non-streaming fallback for transport or stream-close failures', (error) => {
    expect(decideStreamFailure(error)).toBe('non-streaming-fallback')
  })
})

describe('askStream', () => {
  it('streams an anonymous mock assessment without an authorization header', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('', {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await askStream(
      'What is stale?',
      vi.fn(),
      null,
      'PT-1042',
      'NCT99004324',
    )

    const request = fetchMock.mock.calls[0][1] as RequestInit
    expect(request.headers).toEqual({
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
    })
  })

  it('sends case context with a free-form streaming question', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('', {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await askStream(
      'What is stale?',
      vi.fn(),
      'delegated-token',
      'PT-1042',
      'NCT99004324',
    )

    const request = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(request.body))).toEqual({
      question: 'What is stale?',
      patientId: 'PT-1042',
      trialId: 'NCT99004324',
    })
  })

  it('identifies an error before any progress as a preflight failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('event: error\ndata: {"message":"Name a patient in the question."}\n\n', {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        }),
      ),
    )

    await expect(askStream('Who is eligible?', vi.fn(), 'delegated-token')).rejects.toMatchObject({
      name: 'StreamError',
      message: 'Name a patient in the question.',
      receivedProgress: false,
    } satisfies Partial<StreamError>)
  })

  it('marks a terminal error after retrieval progress so the UI can recover without resetting', async () => {
    const onEvent = vi.fn()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          [
            'event: plan',
            'data: {"steps":["Checking patient"]}',
            '',
            'event: source_result',
            'data: {"source":"work","status":"complete","summary":"Done","citations":[],"durationMs":10}',
            '',
            'event: error',
            'data: {"message":"Fabric eligibility evaluation failed"}',
            '',
          ].join('\n'),
          {
            status: 200,
            headers: { 'Content-Type': 'text/event-stream' },
          },
        ),
      ),
    )

    await expect(
      askStream('Is PT-1042 eligible for NCT99004324?', onEvent, 'delegated-token'),
    ).rejects.toMatchObject({
      name: 'StreamError',
      message: 'Fabric eligibility evaluation failed',
      receivedProgress: true,
    } satisfies Partial<StreamError>)
    expect(onEvent).toHaveBeenCalledTimes(2)
  })
})
