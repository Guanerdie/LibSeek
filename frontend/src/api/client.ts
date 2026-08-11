import type {
  AdapterManifest,
  AutomationDecision,
  AutomationDecisionOutcome,
  AutomationPolicy,
  AutomationPolicyRevision,
  AutomationStage,
  ApprovalRequest,
  CsrfResponse,
  DiscoveryRun,
  DownloadExecution,
  DownloadExecutionStatus,
  DownloadJob,
  DownloadJobStatus,
  DownloadJobSummary,
  DownloadJobTimeline,
  DownloadLaunchMode,
  DownloadPlan,
  ExecutionIntent,
  IdentityReview,
  LoginResponse,
  MediaImportApproveRequest,
  MediaImportCreateRequest,
  MediaImportRequest,
  MediaImportRequestSummary,
  MediaImportStatus,
  MediaItem,
  MetadataMatch,
  Page,
  Principal,
  PtSiteCatalog,
  SystemStatus,
  QbStatus,
  QbTorrent,
  TorrentCandidateResult,
  TorrentSearchCreateRequest,
  TorrentSearchRun,
  CreateAutomationPolicyRevision,
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

export const ptSiteApi = {
  catalog: () => request<PtSiteCatalog>('/api/pt-sites/catalog'),
}

export const torrentApi = {
  create: (
    mediaId: string,
    payload: TorrentSearchCreateRequest,
  ) =>
    request<TorrentSearchRun & { job_id: string; deduplicated: boolean }>(
      `/api/media/${encodeURIComponent(mediaId)}/torrent-searches`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
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

export const executionApi = {
  createIntent: (
    approvalId: string,
    launchMode: DownloadLaunchMode,
    expiresInSeconds?: number,
  ) =>
    request<ExecutionIntent>(
      `/api/approval-requests/${encodeURIComponent(approvalId)}/execution-intents`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          launch_mode: launchMode,
          ...(expiresInSeconds === undefined ? {} : { expires_in_seconds: expiresInSeconds }),
        }),
      },
    ),
  execute: (
    approvalId: string,
    intentId: string,
    nonce: string,
    idempotencyKey: string,
  ) =>
    request<DownloadExecution>(
      `/api/approval-requests/${encodeURIComponent(approvalId)}/execute`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({ intent_id: intentId, nonce }),
      },
    ),
  forApproval: (approvalId: string) =>
    request<DownloadExecution>(
      `/api/approval-requests/${encodeURIComponent(approvalId)}/download-execution`,
    ),
  list: (params: {
    page: number
    pageSize: number
    status?: DownloadExecutionStatus
  }) =>
    request<Page<DownloadExecution>>(
      `/api/download-executions?${queryString({
        page: params.page,
        page_size: params.pageSize,
        status: params.status,
      })}`,
    ),
  get: (executionId: string) =>
    request<DownloadExecution>(`/api/download-executions/${encodeURIComponent(executionId)}`),
  reconcile: (executionId: string, reason?: string) =>
    request<DownloadExecution>(
      `/api/download-executions/${encodeURIComponent(executionId)}/reconcile`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: reason?.trim() || null }),
      },
    ),
}

export const downloadJobApi = {
  list: (params: { page: number; pageSize: number; status?: DownloadJobStatus }) =>
    request<Page<DownloadJob>>(
      `/api/download-jobs?${queryString({
        page: params.page,
        page_size: params.pageSize,
        status: params.status,
      })}`,
    ),
  get: (jobId: string) =>
    request<DownloadJob>(`/api/download-jobs/${encodeURIComponent(jobId)}`),
  summary: (jobId: string) =>
    request<DownloadJobSummary>(`/api/download-jobs/${encodeURIComponent(jobId)}/summary`),
  timeline: (jobId: string) =>
    request<DownloadJobTimeline>(`/api/download-jobs/${encodeURIComponent(jobId)}/timeline`),
}

export const mediaImportApi = {
  list: (params: {
    page: number
    pageSize: number
    status?: MediaImportStatus
    downloadJobId?: string
  }) =>
    request<Page<MediaImportRequestSummary>>(
      `/api/media-import-requests?${queryString({
        page: params.page,
        page_size: params.pageSize,
        status: params.status,
        download_job_id: params.downloadJobId?.trim(),
      })}`,
    ),
  get: (requestId: string) =>
    request<MediaImportRequest>(
      `/api/media-import-requests/${encodeURIComponent(requestId)}`,
    ),
  create: (payload: MediaImportCreateRequest) =>
    request<MediaImportRequest>('/api/media-import-requests', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  approve: (requestId: string, payload: MediaImportApproveRequest) =>
    request<MediaImportRequest>(
      `/api/media-import-requests/${encodeURIComponent(requestId)}/approve`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    ),
  reject: (requestId: string, reason?: string) =>
    request<MediaImportRequest>(
      `/api/media-import-requests/${encodeURIComponent(requestId)}/reject`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: reason?.trim() || null }),
      },
    ),
  revoke: (requestId: string, reason?: string) =>
    request<MediaImportRequest>(
      `/api/media-import-requests/${encodeURIComponent(requestId)}/revoke`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: reason?.trim() || null }),
      },
    ),
}

export const automationApi = {
  policy: () => request<AutomationPolicy>('/api/automation/policy'),
  revisions: (params: { page: number; pageSize: number }) =>
    request<Page<AutomationPolicyRevision>>(
      `/api/automation/policy-revisions?${queryString({
        page: params.page,
        page_size: params.pageSize,
      })}`,
    ),
  publishRevision: (payload: CreateAutomationPolicyRevision) =>
    request<AutomationPolicy>('/api/automation/policy-revisions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  decisions: (params: {
    page: number
    pageSize: number
    stage?: AutomationStage
    outcome?: AutomationDecisionOutcome
    mediaItemId?: string
  }) =>
    request<Page<AutomationDecision>>(
      `/api/automation/decisions?${queryString({
        page: params.page,
        page_size: params.pageSize,
        stage: params.stage,
        outcome: params.outcome,
        media_item_id: params.mediaItemId?.trim(),
      })}`,
    ),
  decision: (decisionId: string) =>
    request<AutomationDecision>(
      `/api/automation/decisions/${encodeURIComponent(decisionId)}`,
    ),
}

export const qbApi = {
  status: () => request<QbStatus>('/api/downloaders/qbittorrent/status'),
  torrents: () =>
    request<{ items: QbTorrent[]; total: number }>('/api/downloaders/qbittorrent/torrents'),
}
