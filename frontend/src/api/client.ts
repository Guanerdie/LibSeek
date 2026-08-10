import type {
  AdapterManifest,
  ApprovalRequest,
  DiscoveryRun,
  DownloadPlan,
  IdentityReview,
  MediaItem,
  MetadataMatch,
  Page,
  SystemStatus,
  QbStatus,
  QbTorrent,
  TorrentCandidateResult,
  TorrentSearchRun,
} from '../types'

interface ErrorPayload {
  error_code?: string
  message?: string
}

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers: { Accept: 'application/json', ...init?.headers },
      credentials: 'same-origin',
    })
  } catch {
    throw new ApiError('NETWORK_ERROR', '无法连接后端服务', 0)
  }
  if (!response.ok) {
    let payload: ErrorPayload = {}
    try {
      payload = (await response.json()) as ErrorPayload
    } catch {
      // Deliberately ignore untrusted non-JSON error bodies.
    }
    throw new ApiError(
      payload.error_code ?? 'API_ERROR',
      payload.message ?? '请求失败',
      response.status,
    )
  }
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
  confirmIdentity: (id: string, metadataMatchId: string, operator: string) =>
    request<IdentityReview>(`/api/media/${encodeURIComponent(id)}/identity-confirmations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ metadata_match_id: metadataMatchId, operator }),
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
    preferences: {
      preferred_resolutions: string[]
      preferred_sources: string[]
      preferred_audio: string[]
      preferred_subtitles: string[]
      max_size_bytes?: number
    },
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
  get: (searchId: string) =>
    request<TorrentSearchRun>(`/api/torrent-searches/${encodeURIComponent(searchId)}`),
  candidates: (searchId: string) =>
    request<TorrentCandidateResult[]>(
      `/api/torrent-searches/${encodeURIComponent(searchId)}/candidates`,
    ),
}

export const approvalApi = {
  create: (candidateId: string, operator: string, expiresInMinutes: number) =>
    request<ApprovalRequest>(
      `/api/candidates/${encodeURIComponent(candidateId)}/approval-requests`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operator, expires_in_minutes: expiresInMinutes }),
      },
    ),
  list: (candidateId?: string) =>
    request<ApprovalRequest[]>(
      `/api/approval-requests${candidateId ? `?${queryString({ candidate_id: candidateId })}` : ''}`,
    ),
  get: (approvalId: string) =>
    request<ApprovalRequest>(`/api/approval-requests/${encodeURIComponent(approvalId)}`),
  preflight: (approvalId: string, operator: string) =>
    request<ApprovalRequest>(
      `/api/approval-requests/${encodeURIComponent(approvalId)}/preflight`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operator }),
      },
    ),
  approve: (
    approvalId: string,
    payload: {
      operator: string
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
  reject: (approvalId: string, operator: string, reason?: string) =>
    request<ApprovalRequest>(
      `/api/approval-requests/${encodeURIComponent(approvalId)}/reject`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operator, reason: reason || null }),
      },
    ),
  revoke: (approvalId: string, operator: string, reason?: string) =>
    request<ApprovalRequest>(
      `/api/approval-requests/${encodeURIComponent(approvalId)}/revoke`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operator, reason: reason || null }),
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
