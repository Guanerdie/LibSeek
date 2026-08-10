import type {
  AdapterManifest,
  ApprovalRequest,
  CsrfResponse,
  DiscoveryRun,
  DownloadPlan,
  IdentityReview,
  LoginResponse,
  MediaItem,
  MetadataMatch,
  Page,
  Principal,
  SystemStatus,
  QbStatus,
  QbTorrent,
  TorrentCandidateResult,
  TorrentSearchPreferences,
  TorrentSearchRun,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase()
  const mutatesState = !['GET', 'HEAD', 'OPTIONS'].includes(method)
  if (mutatesState && !csrfToken) {
    throw new ApiError('CSRF_TOKEN_REQUIRED', '状态变更请求缺少 CSRF 令牌', 0)
  }

  const headers = new Headers(init?.headers)
  if (!headers.has('Accept')) headers.set('Accept', 'application/json')
  if (mutatesState && csrfToken) headers.set('X-CSRF-Token', csrfToken)

  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers,
      credentials: 'same-origin',
    })
  } catch (caught) {
    if (caught instanceof Error && caught.name === 'AbortError') throw caught
    throw new ApiError('NETWORK_ERROR', '无法连接后端服务', 0)
  }
  if (!response.ok) {
    let payload: ErrorPayload = {}
    try {
      payload = (await response.json()) as ErrorPayload
    } catch {
      // Deliberately ignore untrusted non-JSON error bodies.
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

export const systemApi = {
  status: () => request<SystemStatus>('/api/system/status'),
}

export const authApi = {
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

export const mediaApi = {
  list: (params: {
    page: number
    pageSize: number
    mediaType?: string
    confidence?: string
    query?: string
  }) =>
    request<Page<MediaItem>>(
      `/api/media?${queryString({
        page: params.page,
        page_size: params.pageSize,
        media_type: params.mediaType,
        identity_confidence: params.confidence,
        query: params.query,
      })}`,
    ),
  get: (id: string) => request<MediaItem>(`/api/media/${encodeURIComponent(id)}`),
  resolve: (id: string) =>
    request<{ media_id: string; job_id: string; status: string; deduplicated: boolean }>(
      `/api/media/${encodeURIComponent(id)}/resolve`,
      { method: 'POST' },
    ),
  metadataCandidates: (id: string) =>
    request<MetadataMatch[]>(`/api/media/${encodeURIComponent(id)}/metadata-candidates`),
  confirmIdentity: (id: string, metadataMatchId: string) =>
    request<IdentityReview>(`/api/media/${encodeURIComponent(id)}/identity-confirmations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ metadata_match_id: metadataMatchId }),
    }),
}

export const discoveryApi = {
  list: (page: number, pageSize: number) =>
    request<Page<DiscoveryRun>>(
      `/api/discovery-runs?${queryString({ page, page_size: pageSize })}`,
    ),
  get: (id: string) => request<DiscoveryRun>(`/api/discovery-runs/${encodeURIComponent(id)}`),
  create: () => request<DiscoveryRun>('/api/discovery-runs', { method: 'POST' }),
}

export const adapterApi = {
  list: () => request<AdapterManifest[]>('/api/adapters'),
}

export const torrentApi = {
  create: (
    mediaId: string,
    preferences: TorrentSearchPreferences,
  ) =>
    request<TorrentSearchRun & { job_id: string; deduplicated: boolean }>(
      `/api/media/${encodeURIComponent(mediaId)}/torrent-searches`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(preferences),
      },
    ),
  list: (mediaId: string) =>
    request<TorrentSearchRun[]>(`/api/media/${encodeURIComponent(mediaId)}/torrent-searches`),
  get: (searchId: string, signal?: AbortSignal) =>
    request<TorrentSearchRun>(`/api/torrent-searches/${encodeURIComponent(searchId)}`, { signal }),
  candidates: (searchId: string, signal?: AbortSignal) =>
    request<TorrentCandidateResult[]>(
      `/api/torrent-searches/${encodeURIComponent(searchId)}/candidates`,
      { signal },
    ),
}

export const approvalApi = {
  create: (candidateId: string, expiresInMinutes: number) =>
    request<ApprovalRequest>(
      `/api/candidates/${encodeURIComponent(candidateId)}/approval-requests`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expires_in_minutes: expiresInMinutes }),
      },
    ),
  list: (candidateId?: string) =>
    request<ApprovalRequest[]>(
      `/api/approval-requests${candidateId ? `?${queryString({ candidate_id: candidateId })}` : ''}`,
    ),
  get: (approvalId: string) =>
    request<ApprovalRequest>(`/api/approval-requests/${encodeURIComponent(approvalId)}`),
  preflight: (approvalId: string) =>
    request<ApprovalRequest>(
      `/api/approval-requests/${encodeURIComponent(approvalId)}/preflight`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      },
    ),
  approve: (
    approvalId: string,
    payload: {
      acknowledges_hnr: boolean
      acknowledges_seeding: boolean
      acknowledges_plan_only: boolean
    },
  ) =>
    request<ApprovalRequest>(
      `/api/approval-requests/${encodeURIComponent(approvalId)}/approve`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    ),
  reject: (approvalId: string, reason?: string) =>
    request<ApprovalRequest>(
      `/api/approval-requests/${encodeURIComponent(approvalId)}/reject`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: reason || null }),
      },
    ),
  revoke: (approvalId: string, reason?: string) =>
    request<ApprovalRequest>(
      `/api/approval-requests/${encodeURIComponent(approvalId)}/revoke`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: reason || null }),
      },
    ),
  plan: (approvalId: string) =>
    request<DownloadPlan>(
      `/api/approval-requests/${encodeURIComponent(approvalId)}/download-plan`,
    ),
}

export const qbApi = {
  status: () => request<QbStatus>('/api/downloaders/qbittorrent/status'),
  torrents: () =>
    request<{ items: QbTorrent[]; total: number }>('/api/downloaders/qbittorrent/torrents'),
}
