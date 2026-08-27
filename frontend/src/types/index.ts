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

export interface AuthSetupStatus {
  admin_initialized: boolean
  configuration_complete: boolean
}

export type PtSiteArchitecture = 'avistaz' | 'nexusphp'

export interface PtSiteArchitectureField {
  name: string
  label: string
  input_type: 'text' | 'url' | 'password'
  required: boolean
  secret: boolean
}

export interface PtSiteArchitectureOption {
  architecture: PtSiteArchitecture
  label: string
  runtime_supported: boolean
  connection_test_supported?: boolean
  fields: PtSiteArchitectureField[]
}

export interface AvistaZConfigurationSnapshot {
  architecture: 'avistaz'
  base_url: string
  username: string
  password_configured: boolean
  pid_configured: boolean
  configured: boolean
  runtime_supported: boolean
  search_ready: boolean
}

export interface NexusPhpConfigurationSnapshot {
  architecture: 'nexusphp'
  site_id: string
  display_name: string
  base_url: string
  profile_id: string | null
  cookie_configured: boolean
  passkey_configured: boolean
  configured: boolean
  runtime_supported: boolean
  search_ready: boolean
}

export type PtSiteConfigurationSnapshot =
  | AvistaZConfigurationSnapshot
  | NexusPhpConfigurationSnapshot

export interface ConfigurationSnapshot {
  nextfind: {
    base_url: string
    username: string
    password_configured: boolean
    configured: boolean
  }
  tmdb: { configured: boolean }
  outbound_proxy: {
    url: string
    username: string
    password_configured: boolean
    configured: boolean
  }
  pt_site: PtSiteConfigurationSnapshot | null
  pt_sites?: {
    avistaz: AvistaZConfigurationSnapshot | null
    nexusphp: NexusPhpConfigurationSnapshot | null
  }
  pt_site_architectures?: PtSiteArchitectureOption[]
  qbittorrent: {
    url: string
    username: string
    save_path: string
    category: string
    configured: boolean
    allow_insecure_http: boolean
  }
  configuration_complete: boolean
}

export interface ConfigurationUpdateRequest {
  nextfind?: { base_url: string; username: string; password?: string }
  tmdb?: { token?: string }
  outbound_proxy?: { url: string; username: string; password?: string }
  pt_site?:
    | {
        architecture: 'avistaz'
        base_url: string
        username: string
        pid?: string
        password?: string
      }
    | {
        architecture: 'nexusphp'
        site_id: string
        display_name: string
        base_url: string
        cookie?: string
        passkey?: string
      }
  qbittorrent?: {
    url: string
    username: string
    save_path: string
    category: string
    allow_insecure_http: boolean
    password?: string
  }
}

export type ConfigurationSection =
  | 'nextfind'
  | 'tmdb'
  | 'outbound_proxy'
  | 'pt_site'
  | 'qbittorrent'

export interface ConfigurationTestResult {
  target: ConfigurationSection
  healthy: boolean
  error_code: string | null
  message: string
}

export interface Page<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

export type DailyMediaState =
  | 'MISSING'
  | 'IDENTIFYING'
  | 'READY'
  | 'SEARCHING'
  | 'CANDIDATES'
  | 'DOWNLOADING'
  | 'COMPLETE'
  | 'NEEDS_ATTENTION'

export type DailyMediaType = 'movie' | 'tv'
export type DailyMediaRegion = '欧美' | '大陆' | '港台' | '韩国' | '日本' | '亚太'

export interface DailyMediaQuery {
  page?: number
  pageSize?: number
  state?: DailyMediaState
  mediaType?: DailyMediaType
  region?: DailyMediaRegion
  year?: number
  query?: string
}

export interface DailyMediaFilterOptions {
  media_types: DailyMediaType[]
  regions: DailyMediaRegion[]
  states: DailyMediaState[]
  years: number[]
}

export type DailySearchState = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'

export type DailyDownloadState =
  | 'SUBMITTING'
  | 'QUEUED'
  | 'DOWNLOADING'
  | 'PAUSED'
  | 'SEEDING'
  | 'COMPLETED'
  | 'ERROR'
  | 'OUTCOME_UNKNOWN'

export interface DailyEpisode {
  id: string
  season_number: number
  episode_number: number
  title: string | null
  air_date: string | null
  state: 'MISSING' | 'AVAILABLE' | 'DOWNLOADING'
}

export interface DailyMedia {
  id: string
  source: string
  source_item_id: string
  media_type: DailyMediaType
  tmdb_id: number | null
  title: string
  original_title: string | null
  country_codes: string[]
  original_language: string | null
  regions: DailyMediaRegion[]
  year: number | null
  poster_path: string | null
  state: DailyMediaState
  attention_reason: string | null
  discovered_at: string
  updated_at: string
}

export interface DailyMediaPage extends Page<DailyMedia> {
  filter_options: DailyMediaFilterOptions
}

export interface DailyMediaDetail extends DailyMedia {
  episodes: DailyEpisode[]
  latest_search: DailySearch | null
}

export interface DailyCandidate {
  id: string
  search_id: string
  site_id: string
  torrent_id: string
  title: string
  details_url: string | null
  size_bytes: number | null
  seeders: number | null
  resolution: string | null
  source: string | null
  codec: string | null
  download_factor: number | null
  season_coverage: number[]
  episode_coverage: string[]
  score: number
  reasons: string[]
  warnings: string[]
}

export interface DailySearch {
  id: string
  media_id: string
  site_ids: string[]
  state: DailySearchState
  error_message: string | null
  created_at: string
  finished_at: string | null
  candidates?: DailyCandidate[]
}

export interface DailyDownload {
  id: string
  media_id: string
  candidate_id: string
  info_hash: string | null
  name: string
  state: DailyDownloadState
  progress: number
  download_speed: number
  upload_speed: number
  ratio: number
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface AutomationPolicy {
  enabled: boolean
  dry_run: boolean
  auto_identify: boolean
  scope_mode: 'filters' | 'selected'
  regions: DailyMediaRegion[]
  selected_media_ids: string[]
  site_ids: string[]
  media_types: DailyMediaType[]
  minimum_score: number
  minimum_seeders: number
  max_size_bytes: number | null
  allow_warnings: boolean
  interval_minutes: number
  retry_delay_minutes: number
  max_attempts: number
  daily_download_limit: number
  daily_download_bytes: number | null
  updated_at: string
  last_run_at: string | null
}

export type AutomationJobState = 'PENDING' | 'RUNNING' | 'RETRY_WAIT' | 'SUCCEEDED' | 'FAILED'

export interface AutomationJob {
  id: string
  run_id: string
  media_id: string
  media_title: string
  state: AutomationJobState
  search_id: string | null
  selected_candidate_id: string | null
  download_id: string | null
  trigger: string
  attempt_count: number
  next_attempt_at: string | null
  decision: {
    mode?: string
    candidate_count?: number
    selected_title?: string | null
    selected_score?: number | null
    rejected?: Array<{ candidate_id: string | null; title: string; reasons: string[] }>
    download_skipped?: string
    download_state?: DailyDownloadState
  }
  error_message: string | null
  created_at: string
  finished_at: string | null
}

export type AutomationRunState = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'

export interface AutomationRun {
  id: string
  trigger: string
  state: AutomationRunState
  created: number
  succeeded: number
  failed: number
  deferred: number
  error_message: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export type AutomationRunPage = Page<AutomationRun>

export type AutomationJobPage = Page<AutomationJob>
