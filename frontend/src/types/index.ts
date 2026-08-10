export type JobStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'RETRY_WAIT'
  | 'CANCELLED'

export type AuthRole = 'viewer' | 'operator' | 'admin'

export interface Principal {
  username: string
  role: AuthRole
}

export interface LoginResponse extends Principal {
  csrf_token: string
}

export interface CsrfResponse {
  csrf_token: string
}

export interface Page<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

export interface ComponentStatus {
  healthy: boolean
  message: string
  checked_at: string
}

export interface SystemStatus {
  api: ComponentStatus
  worker: ComponentStatus
  postgres: ComponentStatus
  nextfind_configured: boolean
  tmdb_configured: boolean
  tmdb_live_enabled: boolean
  avistaz_configured: boolean
  avistaz_live_enabled: boolean
  avistaz_status: string
  qb_configured: boolean
  qb_read_only_enabled: boolean
  qb_status: string
}

export interface MediaItem {
  id: string
  source: string
  source_item_id: string
  media_type: 'movie' | 'tv'
  tmdb_id: number | null
  title: string
  original_title: string | null
  year: number | null
  poster_path: string | null
  raw_type: string | null
  local_episodes: number | null
  total_episodes: number | null
  aired_episodes: number | null
  missing_episodes: string[] | null
  discovery_status: string
  identity_confidence: 'HIGH' | 'NEEDS_CONFIRMATION'
  metadata_status: string
  workflow_status:
    | 'DISCOVERED'
    | 'METADATA_PENDING'
    | 'IDENTITY_REVIEW'
    | 'IDENTITY_CONFIRMED'
    | 'PT_SEARCH_PENDING'
    | 'PT_SEARCHING'
    | 'TORRENT_REVIEW'
    | 'NO_CANDIDATE'
    | 'SEARCH_FAILED'
  discovered_at: string
  updated_at: string
}

export interface AuditEvent {
  id: string
  event_type: string
  entity_type: string
  entity_id: string
  sanitized_details: Record<string, unknown>
  created_at: string
}

export interface DiscoveryRun {
  id: string
  source: string
  status: JobStatus
  started_at: string | null
  finished_at: string | null
  discovered_count: number
  created_count: number
  updated_count: number
  error_code: string | null
  error_message: string | null
  created_at: string
  deduplicated?: boolean
  audit_events?: AuditEvent[]
}

export interface AdapterManifest {
  id: string
  name: string
  adapter_type: string
  version: string
  enabled: boolean
  mode: string
  description: string
  capabilities: Record<string, boolean>
}

export interface MetadataRecord {
  tmdb_id: number
  imdb_id: string | null
  media_type: 'movie' | 'tv'
  title: string
  chinese_title: string | null
  english_title: string | null
  original_title: string | null
  original_language: string | null
  aliases: string[]
  year: number | null
  number_of_seasons: number | null
  number_of_episodes: number | null
  episode_matrix: Record<string, number[]>
  poster_path: string | null
  backdrop_path: string | null
  status: string | null
  confidence: number
  external_ids: Record<string, string>
}

export interface MetadataMatch {
  id: string
  media_id: string
  tmdb_id: number
  rank: number
  score: number
  match_reasons: string[]
  conflicts: string[]
  candidate: MetadataRecord
  created_at: string
}

export interface IdentityReview {
  id: string
  media_id: string
  metadata_match_id: string
  status: string
  confirmed_by: string
  candidate: MetadataRecord
  created_at: string
}

export interface TorrentSearchRun {
  id: string
  media_id: string
  site_id: string
  status: MediaItem['workflow_status']
  strategy_log: Array<Record<string, unknown>>
  sanitized_request: Record<string, unknown>
  candidate_count: number
  error_code: string | null
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface TorrentSearchPreferences {
  preferred_resolutions: string[]
  preferred_sources: string[]
  preferred_audio: string[]
  preferred_subtitles: string[]
  max_size_bytes?: number
}

export interface TorrentCandidate {
  site_id: string
  torrent_id: string
  release_title: string
  details_ref: string
  media_type: 'movie' | 'tv'
  tmdb_id: number | null
  imdb_id: string | null
  year: number | null
  season: number | null
  episodes: number[] | null
  collection_type: string | null
  resolution: string | null
  source: string | null
  codec: string | null
  hdr: string[] | null
  audio: string[] | null
  subtitles: string[] | null
  size_bytes: number | null
  file_count: number | null
  seeders: number | null
  leechers: number | null
  completed: number | null
  download_factor: number | null
  upload_factor: number | null
  hit_and_run: boolean | null
  info_hash: string | null
  published_at: string | null
  match_score: number | null
  match_reasons: string[]
  warnings: string[]
}

export interface TorrentCandidateResult {
  id: string
  search_run_id: string
  candidate: TorrentCandidate
  match_score: number
  match_reasons: string[]
  warnings: string[]
  created_at: string
}

export type ApprovalStatus =
  | 'PENDING'
  | 'APPROVED'
  | 'REJECTED'
  | 'EXPIRED'
  | 'REVOKED'
  | 'CONSUMED'

export type PreflightStatus = 'PASS' | 'WARNING' | 'BLOCKED' | 'UNKNOWN'

export interface ApprovalCandidateSnapshot {
  media_item_id: string
  media_title: string
  media_type: 'movie' | 'tv'
  tmdb_id: number | null
  year: number | null
  torrent_candidate_id: string
  site_id: string
  torrent_id: string
  torrent_ref: string
  release_title: string
  size_bytes: number | null
  info_hash: string | null
  season: number | null
  episodes: number[] | null
  resolution: string | null
  source: string | null
  subtitles: string[] | null
  seeders: number | null
  promotion: { download_factor: number | null; upload_factor: number | null }
  hit_and_run: boolean | null
  match_score: number
  match_reasons: string[]
  warnings: string[]
  requested_at: string
  expires_at: string
}

export interface PreflightCheck {
  code: string
  status: PreflightStatus
  message: string
  details: Record<string, unknown>
}

export interface PreflightResult {
  overall_status: PreflightStatus
  checks: PreflightCheck[]
  checked_at: string
  policy_fingerprint: string
}

export interface ApprovalEvent {
  id: string
  event_type: string
  from_status: string | null
  to_status: string
  actor: string
  reason: string | null
  sanitized_details: Record<string, unknown>
  created_at: string
}

export interface ApprovalRequest {
  id: string
  media_item_id: string
  torrent_candidate_id: string
  status: ApprovalStatus
  candidate: ApprovalCandidateSnapshot
  snapshot_hash: string
  requested_by: string
  requested_at: string
  expires_at: string
  decided_at: string | null
  preflight_result: PreflightResult | null
  preflight_checked_at: string | null
  events: ApprovalEvent[]
}

export interface DownloadPlan {
  id: string
  approval_id: string
  approval_snapshot_hash: string
  preflight_policy_fingerprint: string
  plan_hash: string
  site_id: string
  torrent_ref: string
  expected_info_hash: string | null
  release_title: string
  save_path_ref: string
  category: string
  tags: string[]
  estimated_size_bytes: number | null
  media_destination_plan: Record<string, unknown>
  preflight_result: PreflightResult
  warnings: string[]
  created_at: string
}

export interface QbStatus {
  connected: boolean
  application_version: string
  web_api_version: string
  torrent_count: number
  category_count: number
  active_seeding_count: number
}

export interface QbTorrent {
  hash: string
  name: string
  size: number
  progress: number
  ratio: number
  state: string
  added_on: number
  completion_on: number
  seeding_time: number
  uploaded: number
  upspeed: number
  category: string
  tags: string
  save_path: string
}
