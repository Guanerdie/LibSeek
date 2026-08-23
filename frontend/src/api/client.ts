import type {
  AuthSetupStatus,
  AutomationJob,
  AutomationPolicy,
  AutomationRunResult,
  ConfigurationSnapshot,
  ConfigurationTestResult,
  ConfigurationUpdateRequest,
  CsrfResponse,
  DailyDownload,
  DailyDownloadState,
  DailyMedia,
  DailyMediaDetail,
  DailyMediaPage,
  DailyMediaQuery,
  DailySearch,
  LoginResponse,
  Page,
  Principal,
} from '../types'

interface ErrorPayload {
  error_code?: string
  message?: string
}

type UnauthorizedHandler = (error: ApiError) => void

let csrfToken: string | null = null
let unauthorizedHandler: UnauthorizedHandler | null = null

export class ApiError extends Error {
  constructor(
    public readonly errorCode: string,
    message: string,
    public readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export function setApiCsrfToken(token: string | null): void {
  csrfToken = token
}

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler
}

async function request<T>(
  path: string,
  init?: RequestInit,
  options?: { csrf?: 'required' | 'omit' },
): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase()
  const mutatesState = !['GET', 'HEAD', 'OPTIONS'].includes(method)
  const requiresCsrf = mutatesState && options?.csrf !== 'omit'
  if (requiresCsrf && !csrfToken) {
    throw new ApiError('CSRF_TOKEN_REQUIRED', '状态变更请求缺少 CSRF 令牌', 0)
  }

  const headers = new Headers(init?.headers)
  if (!headers.has('Accept')) headers.set('Accept', 'application/json')
  if (requiresCsrf && csrfToken) headers.set('X-CSRF-Token', csrfToken)

  let response: Response
  try {
    response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  } catch (caught) {
    if (caught instanceof Error && caught.name === 'AbortError') throw caught
    throw new ApiError('NETWORK_ERROR', '无法连接后端服务', 0)
  }
  if (!response.ok) {
    let payload: ErrorPayload = {}
    try {
      payload = (await response.json()) as ErrorPayload
    } catch {
      // Ignore untrusted non-JSON error bodies.
    }
    const error = new ApiError(
      payload.error_code ?? 'API_ERROR',
      payload.message ?? '请求失败',
      response.status,
    )
    if (response.status === 401 && error.errorCode !== 'AUTH_LOGIN_FAILED') {
      unauthorizedHandler?.(error)
    }
    throw error
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function queryString(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  return params.toString()
}

export const authApi = {
  setupStatus: () => request<AuthSetupStatus>('/api/auth/setup-status'),
  setup: (username: string, password: string) =>
    request<LoginResponse>(
      '/api/auth/setup',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      },
      { csrf: 'omit' },
    ),
  csrf: () => request<CsrfResponse>('/api/auth/csrf'),
  login: (username: string, password: string) =>
    request<LoginResponse>('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }),
  me: () => request<Principal>('/api/auth/me'),
  logout: () => request<void>('/api/auth/logout', { method: 'POST' }),
}

export const configurationApi = {
  get: () => request<ConfigurationSnapshot>('/api/configuration'),
  update: (payload: ConfigurationUpdateRequest) =>
    request<ConfigurationSnapshot>('/api/configuration', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  testNextFind: () =>
    request<ConfigurationTestResult>('/api/configuration/tests/nextfind', { method: 'POST' }),
  testTmdb: () =>
    request<ConfigurationTestResult>('/api/configuration/tests/tmdb', { method: 'POST' }),
  testOutboundProxy: () =>
    request<ConfigurationTestResult>('/api/configuration/tests/outbound-proxy', {
      method: 'POST',
    }),
  testPtSite: (architecture: string) =>
    request<ConfigurationTestResult>(
      `/api/configuration/tests/pt-sites/${encodeURIComponent(architecture)}`,
      { method: 'POST' },
    ),
  testQbittorrent: () =>
    request<ConfigurationTestResult>('/api/configuration/tests/qbittorrent', {
      method: 'POST',
    }),
}

export const dailyApi = {
  syncMedia: () =>
    request<{ created: number; updated: number }>('/api/library/sync', { method: 'POST' }),
  media: (params: DailyMediaQuery = {}) =>
    request<DailyMediaPage>(
      `/api/library?${queryString({
        page: params.page ?? 1,
        page_size: params.pageSize ?? 30,
        state: params.state,
        media_type: params.mediaType,
        region: params.region,
        year: params.year,
        query: params.query,
      })}`,
    ),
  mediaDetail: (mediaId: string) =>
    request<DailyMediaDetail>(`/api/library/${encodeURIComponent(mediaId)}`),
  identify: (mediaId: string, tmdbId?: number) =>
    request<DailyMedia>(`/api/library/${encodeURIComponent(mediaId)}/identify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tmdb_id: tmdbId }),
    }),
  createSearch: (mediaId: string, siteIds: string[]) =>
    request<DailySearch>(`/api/library/${encodeURIComponent(mediaId)}/searches`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ site_ids: siteIds }),
    }),
  search: (searchId: string) =>
    request<DailySearch>(`/api/searches/${encodeURIComponent(searchId)}`),
  downloadCandidate: (candidateId: string, confirmWarnings = false) =>
    request<DailyDownload>(`/api/candidates/${encodeURIComponent(candidateId)}/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm_warnings: confirmWarnings }),
    }),
  retryDownload: (downloadId: string) =>
    request<DailyDownload>(`/api/downloads/${encodeURIComponent(downloadId)}/retry`, {
      method: 'POST',
    }),
  downloads: (
    params: { page?: number; pageSize?: number; state?: DailyDownloadState } = {},
  ) =>
    request<Page<DailyDownload>>(
      `/api/downloads?${queryString({
        page: params.page ?? 1,
        page_size: params.pageSize ?? 30,
        state: params.state,
      })}`,
    ),
  syncDownloads: () =>
    request<{ created: number; updated: number }>('/api/downloads/sync', { method: 'POST' }),
}

export const automationApi = {
  policy: () => request<AutomationPolicy>('/api/automation/policy'),
  updatePolicy: (payload: Omit<AutomationPolicy, 'updated_at' | 'last_run_at'>) =>
    request<AutomationPolicy>('/api/automation/policy', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  jobs: () => request<Page<AutomationJob>>('/api/automation/jobs?page=1&page_size=30'),
  run: () => request<AutomationRunResult>('/api/automation/runs', { method: 'POST' }),
  retry: (jobId: string) =>
    request<AutomationJob>(`/api/automation/jobs/${encodeURIComponent(jobId)}/retry`, {
      method: 'POST',
    }),
}
