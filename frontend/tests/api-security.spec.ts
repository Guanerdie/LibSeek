import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  approvalApi,
  automationApi,
  authApi,
  configurationApi,
  downloadJobApi,
  executionApi,
  mediaApi,
  ptSiteApi,
  setApiCsrfToken,
  setUnauthorizedHandler,
  torrentApi,
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
  it('allows only the one-time administrator setup mutation without an existing CSRF token', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ username: 'first-admin', role: 'admin', csrf_token: 'new-session' }),
    )

    await authApi.setup('first-admin', 'local-password')

    const [path, init] = fetchMock.mock.calls[0] ?? []
    expect(path).toBe('/api/auth/setup')
    expect(init?.method).toBe('POST')
    expect(new Headers(init?.headers).has('X-CSRF-Token')).toBe(false)
    expect(JSON.parse(String(init?.body))).toEqual({
      username: 'first-admin',
      password: 'local-password',
    })
  })

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

  it('protects the configuration update with the in-memory session CSRF token', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(jsonResponse({ configuration_complete: false }))
    setApiCsrfToken('csrf-session')

    await configurationApi.update({
      nextfind: { base_url: 'https://nextfind.example', username: 'nextfind-user' },
      tmdb: {},
      pt_site: {
        architecture: 'avistaz',
        base_url: 'https://avistaz.to',
        username: '',
      },
      qbittorrent: {
        url: '',
        username: '',
        save_path: '',
        category: '',
        allow_insecure_http: false,
      },
    })

    const [path, init] = fetchMock.mock.calls[0] ?? []
    expect(path).toBe('/api/configuration')
    expect(init?.method).toBe('PUT')
    expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('csrf-session')
    expect(String(init?.body)).not.toMatch(/password|token/i)
  })

  it('sends every configuration connection test as a CSRF-protected POST', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(async () =>
      jsonResponse({ target: 'nextfind', healthy: true, error_code: null, message: '连接成功' }),
    )
    setApiCsrfToken('csrf-connection-test')

    await configurationApi.testNextFind()
    await configurationApi.testTmdb()
    await configurationApi.testPtSite('avistaz')
    await configurationApi.testQbittorrent()

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/configuration/tests/nextfind',
      '/api/configuration/tests/tmdb',
      '/api/configuration/tests/pt-sites/avistaz',
      '/api/configuration/tests/qbittorrent',
    ])
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.method).toBe('POST')
      expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('csrf-connection-test')
      expect(init?.body).toBeUndefined()
    }
  })

  it('reads metadata resolution status from the dedicated encoded job endpoint', async () => {
    const fetchMock = vi.mocked(fetch)
    const controller = new AbortController()
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        media_id: 'media /1',
        job_id: 'job /1',
        status: 'RUNNING',
        error_code: null,
        error_message: null,
        created_at: '2026-08-11T00:00:00Z',
        updated_at: '2026-08-11T00:00:01Z',
      }),
    )
    setApiCsrfToken('csrf-secret-value')

    const result = await mediaApi.resolveJob('media /1', 'job /1', controller.signal)

    expect(result.status).toBe('RUNNING')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/media/media%20%2F1/resolve-jobs/job%20%2F1',
      expect.objectContaining({
        cache: 'no-store',
        credentials: 'same-origin',
        signal: controller.signal,
      }),
    )
    const init = fetchMock.mock.calls[0]?.[1]
    expect(new Headers(init?.headers).has('X-CSRF-Token')).toBe(false)
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

  it('protects the single candidate download confirmation with CSRF and idempotency', async () => {
    const fetchMock = vi.mocked(fetch)
    const idempotencyKey = `unin-${'b'.repeat(48)}`
    setApiCsrfToken('csrf-session')
    fetchMock.mockResolvedValueOnce(jsonResponse({ outcome: 'EXECUTION_CREATED' }, 201))

    await approvalApi.confirmDownload(
      'candidate /1',
      'START_IMMEDIATELY',
      idempotencyKey,
    )

    const [path, init] = fetchMock.mock.calls[0] ?? []
    const headers = new Headers(init?.headers)
    expect(path).toBe('/api/candidates/candidate%20%2F1/confirm-download')
    expect(init?.method).toBe('POST')
    expect(headers.get('X-CSRF-Token')).toBe('csrf-session')
    expect(headers.get('Idempotency-Key')).toBe(idempotencyKey)
    expect(JSON.parse(String(init?.body))).toEqual({
      acknowledges_hnr: true,
      acknowledges_seeding: true,
      acknowledges_plan_only: true,
      launch_mode: 'START_IMMEDIATELY',
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

  it('reads the PT catalog and sends an explicit credential-free site search payload', async () => {
    const fetchMock = vi.mocked(fetch)
    setApiCsrfToken('csrf-session')
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ default_site_id: 'avistaz', sites: [] }))
      .mockResolvedValueOnce(jsonResponse({ id: 'search-1' }, 202))

    await ptSiteApi.catalog()
    await torrentApi.create('media/unsafe', {
      site_id: 'fixture-nexus',
      preferred_resolutions: ['1080p'],
      preferred_sources: ['WEB-DL'],
      preferred_audio: ['Japanese'],
      preferred_subtitles: ['Chinese'],
      max_size_bytes: 5_368_709_120,
    })

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/pt-sites/catalog')
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBeUndefined()
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).has('X-CSRF-Token')).toBe(false)
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/media/media%2Funsafe/torrent-searches')
    const mutation = fetchMock.mock.calls[1]?.[1]
    expect(mutation?.method).toBe('POST')
    expect(new Headers(mutation?.headers).get('X-CSRF-Token')).toBe('csrf-session')
    expect(JSON.parse(String(mutation?.body))).toEqual({
      site_id: 'fixture-nexus',
      preferred_resolutions: ['1080p'],
      preferred_sources: ['WEB-DL'],
      preferred_audio: ['Japanese'],
      preferred_subtitles: ['Chinese'],
      max_size_bytes: 5_368_709_120,
    })
    expect(String(mutation?.body)).not.toMatch(/cookie|passkey|token|password|operator|https?:/i)
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
