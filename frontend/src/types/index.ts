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

export interface PtSiteCatalogItem {
  site_id: string
  display_name: string
  description: string
  available_for_search: boolean
  mode: string
  search_modes: string[]
  media_types: Array<'movie' | 'tv'>
  manual_only: boolean
  promotion_metadata: boolean
  hit_and_run_metadata: boolean
  torrent_fetch_enabled: boolean
  unavailable_reason_code: string | null
  unavailable_reason_message: string | null
}

export interface PtSiteCatalog {
  default_site_id: string | null
  sites: PtSiteCatalogItem[]
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

export interface TorrentSearchCreateRequest extends TorrentSearchPreferences {
  site_id: string
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
  | 'EXECUTING'
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

export type DownloadLaunchMode = 'ADD_PAUSED' | 'START_IMMEDIATELY'

export type ExecutionIntentStatus = 'ACTIVE' | 'CONSUMED' | 'EXPIRED' | 'CANCELLED'

export interface ExecutionIntent {
  id: string
  approval_id: string
  nonce: string
  status: ExecutionIntentStatus
  approval_snapshot_hash: string
  plan_hash: string
  qb_target_fingerprint: string
  launch_mode: DownloadLaunchMode
  expires_at: string
  created_at: string
}

export type DownloadExecutionStatus =
  | 'PENDING'
  | 'RETRY_WAIT'
  | 'VALIDATING'
  | 'SUBMITTING'
  | 'SUBMITTED'
  | 'ALREADY_PRESENT'
  | 'OUTCOME_UNKNOWN'
  | 'RECONCILIATION_REQUIRED'
  | 'RECONCILIATION_PENDING'
  | 'FAILED'
  | 'CANCELLED'

export interface DownloadExecution {
  id: string
  approval_id: string
  intent_id: string
  status: DownloadExecutionStatus
  requires_reconciliation: boolean
  approval_snapshot_hash: string
  plan_hash: string
  qb_target_fingerprint: string
  launch_mode: DownloadLaunchMode
  attempts: number
  max_attempts: number
  next_retry_at: string | null
  locked_at: string | null
  actual_info_hash: string | null
  actual_info_hash_v1: string | null
  actual_info_hash_v2: string | null
  actual_size_bytes: number | null
  actual_file_count: number | null
  validated_at: string | null
  submitted_at: string | null
  verified_at: string | null
  error_code: string | null
  error_message: string | null
  requested_by: string
  requested_at: string
  reconciliation_requested_by: string | null
  reconciliation_requested_at: string | null
  reconciliation_reason: string | null
  created_at: string
  updated_at: string
}

export type DownloadJobStatus =
  | 'QUEUED'
  | 'DOWNLOADING'
  | 'PAUSED'
  | 'CHECKING'
  | 'SEEDING'
  | 'COMPLETED'
  | 'MISSING'
  | 'ERROR'

export type HnrStatus = 'UNKNOWN' | 'AT_RISK' | 'SATISFIED'

export interface DownloadJob {
  id: string
  execution_id: string
  approval_id: string
  media_item_id: string
  status: DownloadJobStatus
  release_title: string
  info_hash_v1: string | null
  info_hash_v2: string | null
  save_path_ref: string
  category: string
  size_bytes: number
  file_count: number
  progress: number
  download_speed_bps: number
  upload_speed_bps: number
  downloaded_bytes: number
  uploaded_bytes: number
  ratio: number
  hnr_status: HnrStatus
  started_at: string
  completed_at: string | null
  last_seen_at: string | null
  error_code: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface DownloadJobSummaryMedia {
  id: string
  title: string
  media_type: 'movie' | 'tv'
  tmdb_id: number | null
  year: number | null
  season: number | null
  episodes: number[] | null
}

export interface DownloadJobSummaryApproval {
  id: string
  status: ApprovalStatus
  snapshot_hash: string
  expires_at: string
}

export interface DownloadJobSummaryExecution {
  id: string
  status: DownloadExecutionStatus
  launch_mode: DownloadLaunchMode
  requires_reconciliation: boolean
  actual_info_hash_v1: string | null
  actual_info_hash_v2: string | null
  validated_at: string | null
  submitted_at: string | null
  verified_at: string | null
}

export interface DownloadJobSummary {
  job: DownloadJob
  media: DownloadJobSummaryMedia
  approval: DownloadJobSummaryApproval
  execution: DownloadJobSummaryExecution
  warnings: string[]
}

export interface DownloadJobTimelineItem {
  source: 'approval' | 'execution' | 'job'
  event_type: string
  from_status: string | null
  to_status: string
  actor: string
  sanitized_details: Record<string, unknown>
  created_at: string
}

export interface DownloadJobTimeline {
  job_id: string
  items: DownloadJobTimelineItem[]
}

export type MediaImportStatus =
  | 'PREFLIGHT_REQUIRED'
  | 'REVIEW_REQUIRED'
  | 'APPROVED_PLAN_ONLY'
  | 'REJECTED'
  | 'REVOKED'

export type MediaImportOperation = 'HARDLINK' | 'COPY'

export interface MediaImportManifestFile {
  relative_path: string
  size_bytes: number
}

export interface MediaImportSourceManifest {
  source_root_ref: string
  files: MediaImportManifestFile[]
}

export interface MediaImportTargetEntry {
  source_relative_path: string
  target_relative_path: string
}

export interface MediaImportTargetMapping {
  target_root_ref: string
  files: MediaImportTargetEntry[]
  source_retention: true
  overwrite: false
}

export interface MediaImportCreateRequest {
  download_job_id: string
  proposed_operation: MediaImportOperation
  source_manifest: MediaImportSourceManifest
  target_mapping: MediaImportTargetMapping
}

export interface MediaImportJobSummarySnapshot {
  download_job_id: string
  media_item_id: string
  execution_id: string
  approval_id: string
  job_status: DownloadJobStatus
  progress: number
  info_hash_v1: string | null
  info_hash_v2: string | null
  size_bytes: number
  file_count: number
  completed_at: string | null
  hnr_status: HnrStatus
  media_type: 'movie' | 'tv'
  tmdb_id: number | null
  media_title: string
  media_year: number | null
  execution_status: DownloadExecutionStatus
  actual_info_hash_v1: string | null
  actual_info_hash_v2: string | null
  actual_size_bytes: number
  actual_file_count: number
  verified_at: string
}

export interface MediaImportPlan {
  id: string
  request_id: string
  download_job_id: string
  media_item_id: string
  execution_id: string
  mode: 'PLAN_ONLY_NO_FILE_OPERATION'
  proposed_operation: MediaImportOperation
  source_manifest: MediaImportSourceManifest
  source_manifest_hash: string
  target_mapping: MediaImportTargetMapping
  target_mapping_hash: string
  job_summary_snapshot: MediaImportJobSummarySnapshot
  summary_snapshot_hash: string
  config_fingerprint: string
  plan_hash: string
  created_by: string
  created_at: string
}

export interface MediaImportPreflightCheck {
  code: string
  status: PreflightStatus
  message: string
}

export interface MediaImportPreflightResult {
  overall_status: PreflightStatus
  checked_at: string
  config_fingerprint: string
  checks: MediaImportPreflightCheck[]
}

export interface MediaImportPreflight {
  id: string
  request_id: string
  plan_id: string
  overall_status: PreflightStatus
  inspection_hash: string
  result_hash: string
  preflight_hash: string
  config_fingerprint: string
  result: MediaImportPreflightResult
  checked_by: string
  checked_at: string
  created_at: string
}

export interface MediaImportDecisionAcknowledgements {
  acknowledges_plan_only: true
  acknowledges_source_retention: true
  acknowledges_no_overwrite: true
  acknowledges_hnr: boolean
}

export interface MediaImportApproveRequest {
  acknowledges_plan_only: boolean
  acknowledges_source_retention: boolean
  acknowledges_no_overwrite: boolean
  acknowledges_hnr: boolean
}

export interface MediaImportRequestSummary {
  id: string
  download_job_id: string
  media_item_id: string
  execution_id: string
  status: MediaImportStatus
  requested_by: string
  requested_at: string
  updated_at: string
  plan_id: string
  mode: 'PLAN_ONLY_NO_FILE_OPERATION'
  proposed_operation: MediaImportOperation
  plan_hash: string
  media_type: 'movie' | 'tv'
  tmdb_id: number | null
  media_title: string
  media_year: number | null
  source_root_ref: string
  target_root_ref: string
  file_count: number
  size_bytes: number
  preflight_status: PreflightStatus | null
  preflight_checked_at: string | null
  preflight_hash: string | null
}

export interface MediaImportEvent {
  id: string
  request_id: string
  event_type: string
  from_status: string | null
  to_status: string
  actor: string
  sanitized_details: Record<string, unknown>
  created_at: string
}

export interface MediaImportRequest {
  id: string
  download_job_id: string
  media_item_id: string
  execution_id: string
  status: MediaImportStatus
  requested_by: string
  requested_at: string
  approved_by: string | null
  approved_at: string | null
  rejected_by: string | null
  rejected_at: string | null
  rejection_reason: string | null
  revoked_by: string | null
  revoked_at: string | null
  revocation_reason: string | null
  decision_acknowledgements: MediaImportDecisionAcknowledgements | null
  created_at: string
  updated_at: string
  plan: MediaImportPlan
  preflight: MediaImportPreflight | null
  events: MediaImportEvent[]
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

// Automation API contract. Keep all policy/decision wire-field assumptions in this
// section so a backend contract change can be integrated without touching views.
export type AutomationMode = 'DISABLED' | 'MANUAL' | 'AUTO_IF_ELIGIBLE'

export type AutomationStage =
  | 'IDENTITY'
  | 'TORRENT_SELECTION'
  | 'APPROVAL'
  | 'EXECUTION'

export type AutomationDecisionOutcome =
  | 'ACTION_CREATED'
  | 'MANUAL_REQUIRED'
  | 'DISABLED'
  | 'BLOCKED'
  | 'STALE'
  | 'NOOP'

export interface AutomationPolicyRevision {
  id: string
  revision_no: number
  identity_mode: AutomationMode
  torrent_selection_mode: AutomationMode
  approval_mode: AutomationMode
  execution_mode: AutomationMode
  identity_min_score: number
  identity_min_margin: number
  torrent_min_score: number
  torrent_min_margin: number
  torrent_min_seeders: number
  acknowledges_hnr: boolean
  acknowledges_seeding: boolean
  acknowledges_plan_only: boolean
  acknowledges_add_paused_only: boolean
  previous_policy_hash: string | null
  policy_hash: string
  effective_from: string
  created_by: string
  created_at: string
}

export interface AutomationPolicy {
  scope: 'global'
  version: number
  engine_enabled: boolean
  revision: AutomationPolicyRevision
}

export interface CreateAutomationPolicyRevision {
  base_revision_no: number
  identity_mode: AutomationMode
  torrent_selection_mode: AutomationMode
  approval_mode: AutomationMode
  execution_mode: AutomationMode
  identity_min_score: number
  identity_min_margin: number
  torrent_min_score: number
  torrent_min_margin: number
  torrent_min_seeders: number
  acknowledges_hnr: boolean
  acknowledges_seeding: boolean
  acknowledges_plan_only: boolean
  acknowledges_add_paused_only: boolean
}

export interface AutomationDecision {
  id: string
  policy_revision_id: string
  stage: AutomationStage
  action: string
  outcome: AutomationDecisionOutcome
  media_item_id: string
  metadata_match_id: string | null
  torrent_candidate_id: string | null
  approval_request_id: string | null
  download_execution_id: string | null
  reason_codes: string[]
  evidence_snapshot: Record<string, unknown>
  evidence_hash: string
  actor: string
  created_at: string
}
