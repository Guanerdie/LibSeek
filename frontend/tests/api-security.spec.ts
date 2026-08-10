import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  approvalApi,
  authApi,
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
})
