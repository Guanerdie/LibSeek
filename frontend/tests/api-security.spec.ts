import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  approvalApi,
  automationApi,
  authApi,
  downloadJobApi,
  executionApi,
  mediaApi,
  setApiCsrfToken,
  setUnauthorizedHandler,
} from '../src/api/client'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  setApiCsrfToken(null)
  setUnauthorizedHandler(null)
  vi.stubGlobal('fetch', vi.fn())
})

afterEach(() => {
  setApiCsrfToken(null)
  setUnauthorizedHandler(null)
  vi.unstubAllGlobals()
})

describe('API request security', () => {
  it('adds the in-memory CSRF token to mutations but not reads', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ media_id: 'media-1', job_id: 'job-1', status: 'PENDING', deduplicated: false }))
      .mockResolvedValueOnce(jsonResponse({ username: 'viewer', role: 'viewer' }))
    setApiCsrfToken('csrf-secret-value')

    await mediaApi.resolve('media-1')
    await authApi.me()

    const mutationInit = fetchMock.mock.calls[0]?.[1]
    const readInit = fetchMock.mock.calls[1]?.[1]
    expect(new Headers(mutationInit?.headers).get('X-CSRF-Token')).toBe('csrf-secret-value')
    expect(new Headers(readInit?.headers).has('X-CSRF-Token')).toBe(false)
    expect(mutationInit?.credentials).toBe('same-origin')
    expect(readInit?.credentials).toBe('same-origin')
  })

  it('fails before fetch when a mutation has no CSRF token', async () => {
    await expect(mediaApi.resolve('media-1')).rejects.toMatchObject({
      errorCode: 'CSRF_TOKEN_REQUIRED',
      status: 0,
    })
    expect(fetch).not.toHaveBeenCalled()
  })

  it('accepts an empty 204 logout response', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 204 }))
    setApiCsrfToken('csrf-session')

    await expect(authApi.logout()).resolves.toBeUndefined()
  })

  it('notifies on session 401 responses but not failed credentials', async () => {
    const handler = vi.fn()
    const fetchMock = vi.mocked(fetch)
    setUnauthorizedHandler(handler)
    setApiCsrfToken('csrf-bootstrap')
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ error_code: 'AUTH_SESSION_EXPIRED', message: '会话已过期' }, 401),
      )
      .mockResolvedValueOnce(
        jsonResponse({ error_code: 'AUTH_LOGIN_FAILED', message: '用户名或密码错误' }, 401),
      )

    await expect(authApi.me()).rejects.toBeInstanceOf(ApiError)
    expect(handler).toHaveBeenCalledTimes(1)
    await expect(authApi.login('user', 'wrong')).rejects.toMatchObject({
      errorCode: 'AUTH_LOGIN_FAILED',
    })
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('does not send a caller-supplied operator in identity or approval bodies', async () => {
    const fetchMock = vi.mocked(fetch)
    setApiCsrfToken('csrf-session')
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ status: 'CONFIRMED' }))
      .mockResolvedValueOnce(jsonResponse({ id: 'approval-1' }))

    await mediaApi.confirmIdentity('media-1', 'match-1')
    await approvalApi.create('candidate-1', 60)

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      metadata_match_id: 'match-1',
    })
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      expires_in_minutes: 60,
    })
  })

  it('sends execution idempotency and intent material only in the protected mutation', async () => {
    const fetchMock = vi.mocked(fetch)
    setApiCsrfToken('csrf-session')
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: 'execution-1' }))

    await executionApi.execute(
      'approval-1',
      'intent-1',
      `ei1_${'n'.repeat(32)}`,
      `unin-${'a'.repeat(48)}`,
    )

    const init = fetchMock.mock.calls[0]?.[1]
    const headers = new Headers(init?.headers)
    expect(headers.get('X-CSRF-Token')).toBe('csrf-session')
    expect(headers.get('Idempotency-Key')).toBe(`unin-${'a'.repeat(48)}`)
    expect(JSON.parse(String(init?.body))).toEqual({
      intent_id: 'intent-1',
      nonce: `ei1_${'n'.repeat(32)}`,
    })
  })

  it('uses read-only encoded download job endpoints and the explicit page contract', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse({})))

    await downloadJobApi.list({ page: 2, pageSize: 50, status: 'DOWNLOADING' })
    await downloadJobApi.get('job/unsafe')
    await downloadJobApi.summary('job/unsafe')
    await downloadJobApi.timeline('job/unsafe')

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/download-jobs?page=2&page_size=50&status=DOWNLOADING',
      '/api/download-jobs/job%2Funsafe',
      '/api/download-jobs/job%2Funsafe/summary',
      '/api/download-jobs/job%2Funsafe/timeline',
    ])
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.method).toBeUndefined()
      expect(new Headers(init?.headers).has('X-CSRF-Token')).toBe(false)
    }
  })

  it('keeps automation audit reads read-only and protects flat revision publication', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse({})))

    await automationApi.policy()
    await automationApi.revisions({ page: 2, pageSize: 20 })
    await automationApi.decisions({
      page: 3,
      pageSize: 50,
      stage: 'EXECUTION',
      outcome: 'BLOCKED',
      mediaItemId: 'media/unsafe',
    })
    await automationApi.decision('decision/unsafe')

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/automation/policy',
      '/api/automation/policy-revisions?page=2&page_size=20',
      '/api/automation/decisions?page=3&page_size=50&stage=EXECUTION&outcome=BLOCKED&media_item_id=media%2Funsafe',
      '/api/automation/decisions/decision%2Funsafe',
    ])
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.method).toBeUndefined()
      expect(new Headers(init?.headers).has('X-CSRF-Token')).toBe(false)
    }

    fetchMock.mockClear()
    setApiCsrfToken('csrf-session')
    await automationApi.publishRevision({
      base_revision_no: 7,
      identity_mode: 'MANUAL',
      torrent_selection_mode: 'MANUAL',
      approval_mode: 'AUTO_IF_ELIGIBLE',
      execution_mode: 'MANUAL',
      identity_min_score: 0.95,
      identity_min_margin: 0.1,
      torrent_min_score: 0.9,
      torrent_min_margin: 0.1,
      torrent_min_seeders: 1,
      acknowledges_hnr: true,
      acknowledges_seeding: true,
      acknowledges_plan_only: true,
      acknowledges_add_paused_only: false,
    })

    const init = fetchMock.mock.calls[0]?.[1]
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/automation/policy-revisions')
    expect(init?.method).toBe('POST')
    expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('csrf-session')
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>
    expect(body).not.toHaveProperty('created_by')
    expect(body).not.toHaveProperty('engine_enabled')
    expect(JSON.stringify(body)).not.toContain('START_IMMEDIATELY')
  })
})
