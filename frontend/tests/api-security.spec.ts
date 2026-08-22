import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  authApi,
  dailyApi,
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

describe('active API request security', () => {
  it('allows only the one-time setup mutation without an existing CSRF token', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ username: 'admin', role: 'admin', csrf_token: 'session' }),
    )

    await authApi.setup('admin', 'local-password')

    const [, init] = fetchMock.mock.calls[0] ?? []
    expect(new Headers(init?.headers).has('X-CSRF-Token')).toBe(false)
    await expect(dailyApi.syncMedia()).rejects.toMatchObject({
      errorCode: 'CSRF_TOKEN_REQUIRED',
    })
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('adds the in-memory CSRF token to daily mutations but not reads', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ created: 1, updated: 0 }))
      .mockResolvedValueOnce(jsonResponse({ items: [], page: 1, page_size: 30, total: 0 }))
    setApiCsrfToken('csrf-session')

    await dailyApi.syncMedia()
    await dailyApi.media()

    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get('X-CSRF-Token')).toBe(
      'csrf-session',
    )
    expect(new Headers(fetchMock.mock.calls[1]?.[1]?.headers).has('X-CSRF-Token')).toBe(false)
  })

  it('encodes identifiers before placing them in a request path', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(jsonResponse({}))

    await dailyApi.mediaDetail('media /1')

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/library/media%20%2F1')
  })

  it('reports expired authenticated sessions without treating login failure as expiry', async () => {
    const expired = vi.fn()
    const fetchMock = vi.mocked(fetch)
    setUnauthorizedHandler(expired)
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ error_code: 'AUTH_REQUIRED', message: '需要登录' }, 401))
      .mockResolvedValueOnce(
        jsonResponse({ error_code: 'AUTH_LOGIN_FAILED', message: '用户名或密码错误' }, 401),
      )
    setApiCsrfToken('csrf-session')

    await expect(authApi.me()).rejects.toBeInstanceOf(ApiError)
    await expect(authApi.login('admin', 'wrong')).rejects.toBeInstanceOf(ApiError)

    expect(expired).toHaveBeenCalledOnce()
  })
})
