import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import AdaptersView from '../src/views/AdaptersView.vue'
import ApprovalListView from '../src/views/ApprovalListView.vue'
import ApprovalView from '../src/views/ApprovalView.vue'
import DiscoveryView from '../src/views/DiscoveryView.vue'
import IdentityView from '../src/views/IdentityView.vue'
import MediaView from '../src/views/MediaView.vue'
import QbittorrentView from '../src/views/QbittorrentView.vue'
import TorrentCandidatesView from '../src/views/TorrentCandidatesView.vue'
import { useAuthStore } from '../src/stores/auth'
import { useIdentityStore } from '../src/stores/identity'
import { useMediaStore } from '../src/stores/media'
import type { MetadataMatch } from '../src/types'

const mocks = vi.hoisted(() => ({
  systemStatus: vi.fn(),
  mediaList: vi.fn(),
  mediaGet: vi.fn(),
  mediaResolve: vi.fn(),
  metadataCandidates: vi.fn(),
  confirmIdentity: vi.fn(),
  discoveryList: vi.fn(),
  discoveryGet: vi.fn(),
  discoveryCreate: vi.fn(),
  adapterList: vi.fn(),
  ptSiteCatalog: vi.fn(),
  torrentList: vi.fn(),
  torrentGet: vi.fn(),
  torrentCandidates: vi.fn(),
  torrentCreate: vi.fn(),
  approvalList: vi.fn(),
  approvalGet: vi.fn(),
  approvalCreate: vi.fn(),
  approvalConfirmDownload: vi.fn(),
  approvalPreflight: vi.fn(),
  approvalApprove: vi.fn(),
  approvalReject: vi.fn(),
  approvalRevoke: vi.fn(),
  approvalPlan: vi.fn(),
  executionCreateIntent: vi.fn(),
  executionExecute: vi.fn(),
  executionForApproval: vi.fn(),
  executionList: vi.fn(),
  executionGet: vi.fn(),
  executionDownloadJob: vi.fn(),
  executionReconcile: vi.fn(),
  qbStatus: vi.fn(),
  qbTorrents: vi.fn(),
}))

const routeState = vi.hoisted(() => ({
  params: { id: 'media-1' } as Record<string, string | undefined>,
}))
const routerMock = vi.hoisted(() => ({ replace: vi.fn() }))

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => routerMock,
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  setApiCsrfToken: vi.fn(),
  authApi: {},
  systemApi: { status: mocks.systemStatus },
  mediaApi: {
    list: mocks.mediaList,
    get: mocks.mediaGet,
    resolve: mocks.mediaResolve,
    metadataCandidates: mocks.metadataCandidates,
    confirmIdentity: mocks.confirmIdentity,
  },
  discoveryApi: {
    list: mocks.discoveryList,
    get: mocks.discoveryGet,
    create: mocks.discoveryCreate,
  },
  adapterApi: { list: mocks.adapterList },
  ptSiteApi: { catalog: mocks.ptSiteCatalog },
  torrentApi: {
    list: mocks.torrentList,
    get: mocks.torrentGet,
    candidates: mocks.torrentCandidates,
    create: mocks.torrentCreate,
  },
  approvalApi: {
    confirmDownload: mocks.approvalConfirmDownload,
    list: mocks.approvalList,
    get: mocks.approvalGet,
    create: mocks.approvalCreate,
    preflight: mocks.approvalPreflight,
    approve: mocks.approvalApprove,
    reject: mocks.approvalReject,
    revoke: mocks.approvalRevoke,
    plan: mocks.approvalPlan,
  },
  executionApi: {
    createIntent: mocks.executionCreateIntent,
    execute: mocks.executionExecute,
    forApproval: mocks.executionForApproval,
    list: mocks.executionList,
    get: mocks.executionGet,
    downloadJob: mocks.executionDownloadJob,
    reconcile: mocks.executionReconcile,
  },
  qbApi: {
    status: mocks.qbStatus,
    torrents: mocks.qbTorrents,
  },
}))

const mediaItem = {
  id: 'media-1',
  source: 'nextfind',
  source_item_id: 'nextfind:42',
  media_type: 'tv' as const,
  tmdb_id: 42,
  title: '测试剧集',
  original_title: 'Test Series',
  year: 2026,
  country_codes: ['JP', 'US'],
  poster_path: null,
  raw_type: 'tv',
  local_episodes: 2,
  total_episodes: 8,
  aired_episodes: 6,
  missing_episodes: ['S01E03'],
  discovery_status: 'MISSING',
  identity_confidence: 'HIGH' as const,
  metadata_status: 'RESOLVED',
  workflow_status: 'IDENTITY_REVIEW' as const,
  discovered_at: '2026-08-10T00:00:00Z',
  updated_at: '2026-08-10T00:00:00Z',
}

const ptSiteCatalog = {
  default_site_id: 'avistaz',
  sites: [
    {
      site_id: 'avistaz',
      display_name: 'AvistaZ',
      description: 'AvistaZ 只读候选搜索',
      available_for_search: true,
      mode: 'LIVE_READ_ONLY_SEARCH',
      search_modes: ['TMDB_ID', 'IMDB_ID', 'TEXT'],
      media_types: ['movie', 'tv'],
      manual_only: false,
      promotion_metadata: true,
      hit_and_run_metadata: true,
      torrent_fetch_enabled: false,
      unavailable_reason_code: null,
      unavailable_reason_message: null,
    },
    {
      site_id: 'fixture-nexus',
      display_name: 'Fixture Nexus',
      description: '脱敏 fixture 验证站点',
      available_for_search: false,
      mode: 'DISABLED_BY_DEFAULT',
      search_modes: ['TEXT'],
      media_types: ['movie', 'tv'],
      manual_only: true,
      promotion_metadata: false,
      hit_and_run_metadata: false,
      torrent_fetch_enabled: false,
      unavailable_reason_code: 'NEXUSPHP_SITE_DISABLED',
      unavailable_reason_message: '该站点 Profile 尚未启用',
    },
  ],
}

const syntheticTwoSite = {
  ...ptSiteCatalog.sites[0],
  site_id: 'synthetic-two',
  display_name: 'Synthetic Two',
  description: '完全合成的第二 PT 站点',
  search_modes: ['TEXT'],
  manual_only: true,
}

const run = {
  id: 'run-1',
  source: 'nextfind',
  status: 'SUCCEEDED' as const,
  started_at: '2026-08-10T00:00:00Z',
  finished_at: '2026-08-10T00:01:00Z',
  discovered_count: 3,
  created_count: 2,
  updated_count: 1,
  error_code: null,
  error_message: null,
  created_at: '2026-08-10T00:00:00Z',
}

const approval = {
  id: 'approval-1',
  media_item_id: 'media-1',
  torrent_candidate_id: 'candidate-1',
  status: 'PENDING' as const,
  candidate: {
    media_item_id: 'media-1',
    media_title: '测试剧集',
    media_type: 'tv' as const,
    tmdb_id: 42,
    year: 2026,
    torrent_candidate_id: 'candidate-1',
    site_id: 'avistaz',
    torrent_id: 'torrent-1',
    torrent_ref: 'avistaz:details:safe',
    release_title: 'Test Series S01E03 1080p WEB-DL',
    size_bytes: 2048,
    info_hash: '1'.repeat(40),
    season: 1,
    episodes: [3],
    resolution: '1080p',
    source: 'WEB-DL',
    subtitles: ['Chinese'],
    seeders: 4,
    promotion: { download_factor: 0, upload_factor: 1 },
    hit_and_run: false,
    match_score: 0.93,
    match_reasons: ['TMDB_ID_EXACT', 'EPISODE_COVERAGE_EXACT'],
    warnings: [],
    requested_at: '2026-08-10T00:00:00Z',
    expires_at: '2026-08-10T01:00:00Z',
  },
  snapshot_hash: 'a'.repeat(64),
  requested_by: 'reviewer',
  requested_at: '2026-08-10T00:00:00Z',
  expires_at: '2026-08-10T01:00:00Z',
  decided_at: null,
  preflight_result: {
    overall_status: 'UNKNOWN' as const,
    checked_at: '2026-08-10T00:10:00Z',
    policy_fingerprint: 'b'.repeat(64),
    checks: [
      {
        code: 'HNR_KNOWN',
        status: 'UNKNOWN' as const,
        message: '候选 H&R 规则未知',
        details: {},
      },
    ],
  },
  preflight_checked_at: '2026-08-10T00:10:00Z',
  events: [],
}

const torrentCandidateResult = {
  id: 'candidate-1',
  search_run_id: 'search-1',
  match_score: 0.93,
  match_reasons: ['TMDB_ID_EXACT', 'EPISODE_COVERAGE_EXACT'],
  warnings: [],
  created_at: '2026-08-10T00:01:00Z',
  candidate: {
    site_id: 'avistaz',
    torrent_id: 'torrent-1',
    release_title: 'Test Series S01E03 1080p WEB-DL',
    details_ref: 'avistaz:details:safe',
    media_type: 'tv',
    tmdb_id: 42,
    imdb_id: 'tt0042',
    year: 2026,
    season: 1,
    episodes: [3],
    collection_type: 'episode',
    resolution: '1080p',
    source: 'WEB-DL',
    codec: 'H.265',
    hdr: null,
    audio: ['Japanese'],
    subtitles: ['Chinese'],
    size_bytes: 2048,
    file_count: 1,
    seeders: 4,
    leechers: 1,
    completed: 10,
    download_factor: 0,
    upload_factor: 1,
    hit_and_run: false,
    info_hash: '1'.repeat(40),
    published_at: '2026-08-10T00:00:00Z',
    match_score: 0.93,
    match_reasons: ['TMDB_ID_EXACT'],
    warnings: [],
  },
}

const downloadPlan = {
  id: 'plan-1',
  approval_id: 'approval-1',
  approval_snapshot_hash: 'a'.repeat(64),
  preflight_policy_fingerprint: 'b'.repeat(64),
  plan_hash: 'c'.repeat(64),
  site_id: 'avistaz',
  torrent_ref: 'avistaz:details:safe',
  expected_info_hash: '1'.repeat(40),
  release_title: 'Test Series S01E03 1080p WEB-DL',
  save_path_ref: 'media-tv',
  category: 'media',
  tags: ['unin'],
  estimated_size_bytes: 2048,
  media_destination_plan: {},
  preflight_result: { ...approval.preflight_result, overall_status: 'PASS' as const },
  warnings: [],
  created_at: '2026-08-10T00:11:00Z',
}

const execution = {
  id: 'execution-1',
  approval_id: 'approval-1',
  intent_id: 'intent-1',
  status: 'PENDING' as const,
  requires_reconciliation: false,
  approval_snapshot_hash: 'a'.repeat(64),
  plan_hash: 'c'.repeat(64),
  qb_target_fingerprint: 'd'.repeat(64),
  launch_mode: 'ADD_PAUSED' as const,
  attempts: 0,
  max_attempts: 3,
  next_retry_at: null,
  locked_at: null,
  actual_info_hash: null,
  actual_info_hash_v1: null,
  actual_info_hash_v2: null,
  actual_size_bytes: null,
  actual_file_count: null,
  validated_at: null,
  submitted_at: null,
  verified_at: null,
  error_code: null,
  error_message: null,
  requested_by: 'admin-user',
  requested_at: '2026-08-10T00:12:00Z',
  reconciliation_requested_by: null,
  reconciliation_requested_at: null,
  reconciliation_reason: null,
  created_at: '2026-08-10T00:12:00Z',
  updated_at: '2026-08-10T00:12:00Z',
}

const executionIntent = {
  id: 'intent-1',
  approval_id: 'approval-1',
  nonce: `ei1_${'n'.repeat(32)}`,
  status: 'ACTIVE' as const,
  approval_snapshot_hash: 'a'.repeat(64),
  plan_hash: 'c'.repeat(64),
  qb_target_fingerprint: 'd'.repeat(64),
  launch_mode: 'ADD_PAUSED' as const,
  expires_at: '2026-08-10T00:20:00Z',
  created_at: '2026-08-10T00:12:00Z',
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  mocks.executionDownloadJob.mockRejectedValue(new Error('关联下载任务接口暂不可用'))
  routeState.params = { id: 'media-1' }
  mocks.ptSiteCatalog.mockResolvedValue(ptSiteCatalog)
  mocks.torrentList.mockResolvedValue([])
  mocks.executionForApproval.mockRejectedValue(
    Object.assign(new Error('审批尚未创建下载执行记录'), {
      status: 404,
      errorCode: 'DOWNLOAD_EXECUTION_NOT_FOUND',
    }),
  )
  const auth = useAuthStore()
  auth.principal = { username: 'admin-user', role: 'admin' }
  auth.initialized = true
})

function mockApprovedApprovalRoute(routeKind: 'detail' | 'candidate'): void {
  if (routeKind === 'detail') {
    routeState.params = { id: 'approval-1' }
    mocks.approvalGet.mockResolvedValueOnce({ ...approval, status: 'APPROVED' })
    mocks.approvalPlan.mockResolvedValueOnce(downloadPlan)
    return
  }
  routeState.params = {
    mediaId: 'media-1',
    searchId: 'search-1',
    candidateId: 'candidate-1',
  }
  mocks.mediaGet.mockResolvedValueOnce(mediaItem)
  mocks.torrentCandidates.mockResolvedValueOnce([torrentCandidateResult])
  mocks.approvalList.mockResolvedValueOnce([{ ...approval, status: 'APPROVED' }])
  mocks.approvalPlan.mockResolvedValueOnce(downloadPlan)
}

describe('MediaView', () => {
  it('syncs NextFind from the daily media page and refreshes the list', async () => {
    const completedRun = {
      id: 'run-media-sync',
      source: 'nextfind',
      status: 'SUCCEEDED',
      discovered_count: 3,
      created_count: 2,
      updated_count: 1,
      error_code: null,
      error_message: null,
      started_at: '2026-08-10T00:00:00Z',
      finished_at: '2026-08-10T00:01:00Z',
      created_at: '2026-08-10T00:00:00Z',
      audit_events: [],
      deduplicated: false,
    }
    mocks.mediaList.mockResolvedValue({ items: [mediaItem], page: 1, page_size: 20, total: 1 })
    mocks.discoveryCreate.mockResolvedValueOnce(completedRun)
    const wrapper = mount(MediaView)
    await flushPromises()

    await wrapper.get('.header-actions button').trigger('click')
    await flushPromises()

    expect(mocks.discoveryCreate).toHaveBeenCalledTimes(1)
    expect(mocks.discoveryGet).not.toHaveBeenCalled()
    expect(mocks.mediaList).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('同步完成：新增 2 项，更新 1 项')
  })

  it('shows loading, the missing-media list and pagination', async () => {
    let resolveRequest: (value: unknown) => void = () => undefined
    mocks.mediaList.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveRequest = resolve
      }),
    )
    const wrapper = mount(MediaView)
    await nextTick()
    expect(wrapper.text()).toContain('正在读取数据')
    resolveRequest({ items: [mediaItem], page: 1, page_size: 20, total: 21 })
    await flushPromises()
    expect(wrapper.text()).toContain('测试剧集')
    expect(wrapper.text()).toContain('S01E03')
    expect(wrapper.text()).toContain('42')
    expect(wrapper.get('[data-testid="media-country"]').text()).toBe('日本 / 美国')

    mocks.mediaList.mockResolvedValueOnce({ items: [], page: 2, page_size: 20, total: 21 })
    await wrapper.get('.pagination button:last-child').trigger('click')
    await flushPromises()
    expect(mocks.mediaList).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))
  })

  it('clears stale successful rows before an error is shown', async () => {
    mocks.mediaList.mockResolvedValueOnce({ items: [mediaItem], page: 1, page_size: 20, total: 1 })
    const wrapper = mount(MediaView)
    await flushPromises()
    expect(wrapper.text()).toContain('测试剧集')

    mocks.mediaList.mockRejectedValueOnce(new Error('上游不可用'))
    await useMediaStore().load()
    await flushPromises()
    expect(wrapper.text()).not.toContain('测试剧集')
    expect(wrapper.text()).toContain('上游不可用')
  })

  it('shows a pending country when the source has no country metadata', async () => {
    mocks.mediaList.mockResolvedValueOnce({
      items: [{ ...mediaItem, country_codes: null }],
      page: 1,
      page_size: 20,
      total: 1,
    })
    const wrapper = mount(MediaView)
    await flushPromises()

    expect(wrapper.get('[data-testid="media-country"]').text()).toBe('待确认')
  })

  it('applies a NextFind region filter from the media selection page', async () => {
    mocks.mediaList.mockResolvedValue({ items: [mediaItem], page: 1, page_size: 20, total: 1 })
    const wrapper = mount(MediaView)
    await flushPromises()
    const store = useMediaStore()
    store.page = 3
    mocks.mediaList.mockResolvedValueOnce({ items: [mediaItem], page: 1, page_size: 20, total: 1 })

    await wrapper.get('select[aria-label="地区"]').setValue('japan')
    await wrapper.get('.filter-bar').trigger('submit')
    await flushPromises()

    expect(store.page).toBe(1)
    expect(mocks.mediaList).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 1, region: 'japan' }),
    )
  })

  it('renders the empty state', async () => {
    mocks.mediaList.mockResolvedValueOnce({ items: [], page: 1, page_size: 20, total: 0 })
    const wrapper = mount(MediaView)
    await flushPromises()
    expect(wrapper.text()).toContain('当前没有未入库影视')
  })
})

describe('DiscoveryView', () => {
  it('shows task status, counters and the audit timeline', async () => {
    mocks.discoveryList.mockResolvedValueOnce({ items: [run], page: 1, page_size: 20, total: 1 })
    mocks.discoveryGet.mockResolvedValueOnce({
      ...run,
      audit_events: [
        {
          id: 'audit-1',
          event_type: 'DISCOVERY_RUN_SUCCEEDED',
          entity_type: 'discovery_run',
          entity_id: 'run-1',
          sanitized_details: { discovered_count: 3 },
          created_at: '2026-08-10T00:01:00Z',
        },
      ],
    })
    const wrapper = mount(DiscoveryView)
    await flushPromises()
    expect(wrapper.text()).toContain('刷新进度')
    await wrapper.get('.task-row').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已完成')
    expect(wrapper.text()).toContain('DISCOVERY_RUN_SUCCEEDED')
    expect(wrapper.text()).toContain('发现3')

    mocks.discoveryList.mockResolvedValueOnce({ items: [run], page: 1, page_size: 20, total: 1 })
    mocks.discoveryGet.mockResolvedValueOnce({ ...run, audit_events: [] })
    await wrapper.get('.header-actions .secondary').trigger('click')
    await flushPromises()
    expect(mocks.discoveryList).toHaveBeenCalledTimes(2)
    expect(mocks.discoveryGet).toHaveBeenCalledTimes(2)
  })
})

describe('AdaptersView', () => {
  it('shows adapter capabilities and disabled phase state', async () => {
    mocks.adapterList.mockResolvedValueOnce([
      {
        id: 'avistaz-mock',
        name: 'AvistaZ',
        adapter_type: 'pt_site',
        version: '1.0',
        enabled: false,
        mode: 'MOCK_ONLY',
        description: '本阶段未启用',
        capabilities: { tmdb_search: true, fetch_torrent_enabled: false },
      },
    ])
    const wrapper = mount(AdaptersView)
    await flushPromises()
    expect(wrapper.text()).toContain('AvistaZ')
    expect(wrapper.text()).toContain('tmdb search')
    expect(wrapper.text()).toContain('未启用')
  })
})

describe('IdentityView', () => {
  it('keeps metadata resolution disabled while the current media is loading', async () => {
    let resolveMedia: (value: typeof mediaItem) => void = () => undefined
    mocks.mediaGet.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveMedia = resolve
      }),
    )
    mocks.metadataCandidates.mockResolvedValueOnce([])
    const wrapper = mount(IdentityView)
    await nextTick()

    const resolveButton = wrapper
      .findAll('.header-actions button')
      .find((button) => button.text().includes('重新解析'))
    expect(resolveButton?.attributes('disabled')).toBeDefined()
    await resolveButton?.trigger('click')
    expect(mocks.mediaResolve).not.toHaveBeenCalled()

    resolveMedia(mediaItem)
    await flushPromises()
    expect(wrapper.text()).not.toContain('当前影视身份已变更')
  })

  it('shows source data, TMDB candidates, conflicts and manual confirmation', async () => {
    mocks.mediaGet.mockResolvedValue(mediaItem)
    const candidateWithUnknownEpisodeMatrix = {
      id: 'match-1',
      media_id: 'media-1',
      tmdb_id: 42,
      rank: 1,
      score: 0.92,
      match_reasons: ['TMDB_ID_EXACT', 'YEAR_MATCH'],
      conflicts: ['MEDIA_TYPE_CONFLICT'],
      created_at: '2026-08-10T00:00:00Z',
      candidate: {
        tmdb_id: 42,
        imdb_id: 'tt0042',
        media_type: 'tv',
        title: '测试剧集',
        chinese_title: '测试剧集',
        english_title: 'Test Series',
        original_title: 'Original Series',
        original_language: 'ja',
        country_codes: ['KR'],
        aliases: [],
        year: 2026,
        number_of_seasons: 1,
        number_of_episodes: 8,
        episode_matrix: null,
        poster_path: '/poster.jpg',
        backdrop_path: null,
        status: 'Returning Series',
        confidence: 1,
        external_ids: { imdb_id: 'tt0042' },
      },
    } satisfies MetadataMatch
    mocks.metadataCandidates.mockResolvedValue([candidateWithUnknownEpisodeMatrix])
    mocks.confirmIdentity.mockResolvedValue({ status: 'CONFIRMED' })
    const wrapper = mount(IdentityView)
    await flushPromises()
    expect(useIdentityStore().candidates[0]?.candidate.episode_matrix).toBeNull()
    expect(wrapper.text()).toContain('刷新候选')
    expect(wrapper.text()).toContain('NextFind 标题')
    expect(wrapper.text()).toContain('Test Series')
    expect(wrapper.get('[data-testid="source-country"] strong').text()).toBe('日本 / 美国')
    expect(wrapper.get('[data-testid="candidate-country"]').text()).toBe('韩国')
    expect(wrapper.text()).toContain('TMDB_ID_EXACT')
    expect(wrapper.text()).toContain('MEDIA_TYPE_CONFLICT')

    await wrapper.get('.header-actions .secondary:nth-child(2)').trigger('click')
    await flushPromises()
    expect(mocks.mediaGet).toHaveBeenCalledTimes(2)
    expect(mocks.metadataCandidates).toHaveBeenCalledTimes(2)

    await wrapper.get('.confirm-button').trigger('click')
    await flushPromises()
    expect(mocks.confirmIdentity).toHaveBeenCalledWith('media-1', 'match-1')
  })
})

describe('TorrentCandidatesView', () => {
  it('shows scored candidates and submits a download only after one confirmation', async () => {
    const searchRun = {
      id: 'search-1',
      media_id: 'media-1',
      site_id: 'synthetic-two',
      status: 'TORRENT_REVIEW',
      strategy_log: [{ strategy: 'TMDB_ID', candidate_count: 1 }],
      sanitized_request: {},
      candidate_count: 1,
      error_code: null,
      error_message: null,
      started_at: '2026-08-10T00:00:00Z',
      finished_at: '2026-08-10T00:01:00Z',
      created_at: '2026-08-10T00:00:00Z',
    }
    mocks.ptSiteCatalog.mockResolvedValueOnce({
      ...ptSiteCatalog,
      sites: [...ptSiteCatalog.sites, syntheticTwoSite],
    })
    mocks.mediaGet.mockResolvedValue(mediaItem)
    mocks.torrentList.mockResolvedValue([searchRun])
    mocks.torrentGet.mockResolvedValue(searchRun)
    mocks.torrentCandidates.mockResolvedValue([
      {
        id: 'candidate-1',
        search_run_id: 'search-1',
        match_score: 0.91,
        match_reasons: ['TMDB_ID_EXACT', 'EPISODE_COVERAGE_EXACT'],
        warnings: ['HNR_UNKNOWN'],
        created_at: '2026-08-10T00:01:00Z',
        candidate: {
          site_id: 'synthetic-two',
          torrent_id: 'torrent-1',
          release_title: 'Test Series 2026 S01E03 1080p WEB-DL',
          details_ref: 'synthetic-two:details:safe',
          media_type: 'tv',
          tmdb_id: 42,
          imdb_id: 'tt0042',
          year: 2026,
          season: 1,
          episodes: [3],
          collection_type: 'episode',
          resolution: '1080p',
          source: 'WEB-DL',
          codec: 'H.265',
          hdr: null,
          audio: ['Japanese'],
          subtitles: ['Chinese'],
          size_bytes: 1073741824,
          file_count: 1,
          seeders: 8,
          leechers: 1,
          completed: 10,
          download_factor: 0,
          upload_factor: 1,
          hit_and_run: null,
          info_hash: null,
          published_at: '2026-08-10T00:00:00Z',
          match_score: 0.91,
          match_reasons: ['TMDB_ID_EXACT'],
          warnings: ['HNR_UNKNOWN'],
        },
      },
    ])
    const preflight = {
      overall_status: 'PASS',
      checks: [
        {
          code: 'QB_CONNECTION',
          status: 'PASS',
          message: 'qBittorrent 连接正常',
          details: {},
        },
      ],
      checked_at: '2026-08-10T00:02:00Z',
      policy_fingerprint: 'a'.repeat(64),
    }
    mocks.approvalConfirmDownload.mockResolvedValue({
      outcome: 'EXECUTION_CREATED',
      approval_created: true,
      execution_created: true,
      preflight,
      approval: {
        id: 'approval-quick-1',
        media_item_id: 'media-1',
        torrent_candidate_id: 'candidate-1',
        preflight_result: preflight,
      },
      execution: {
        id: 'execution-quick-1',
        approval_id: 'approval-quick-1',
        status: 'PENDING',
      },
    })
    const wrapper = mount(TorrentCandidatesView)
    await flushPromises()
    expect(wrapper.get('.header-actions').text()).toContain('刷新结果')
    expect(wrapper.get('.phase-banner').text()).toContain('选择候选并确认一次')
    expect(wrapper.get('.phase-banner').text()).toContain('自动完成 qBittorrent 预检')
    expect(wrapper.get('.pt-site-capabilities').text()).toContain('允许策略编排')
    const mediaSummary = wrapper.get('.result-toolbar > div')
    expect(mediaSummary.get('strong').text()).toBe('测试剧集')
    expect(mediaSummary.get('small').text()).toBe(
      'TMDB 42 · 国家 / 地区 日本 / 美国 · 地区分组 欧美 / 日本 · Synthetic Two (synthetic-two)',
    )

    const runOption = wrapper.get('.result-toolbar select option[value="search-1"]')
    expect(runOption.text()).toMatch(
      /^Synthetic Two \(synthetic-two\) · .+ · TORRENT_REVIEW · 1 项$/,
    )

    const siteSelector = wrapper.get('select[aria-label="PT 站点"]')
    expect(siteSelector.get('option[value="avistaz"]').text()).toContain('AvistaZ (avistaz)')
    expect(siteSelector.get('option[value="synthetic-two"]').text()).toBe(
      'Synthetic Two (synthetic-two) · 可搜索',
    )
    expect(siteSelector.get('option[value="fixture-nexus"]').text()).toBe(
      'Fixture Nexus (fixture-nexus) · 已禁用',
    )

    const desktopCandidate = wrapper.get('.torrent-candidate-desktop-list tbody tr')
    expect(desktopCandidate.get('.site-id-badge').text()).toBe(
      'Synthetic Two (synthetic-two)',
    )
    expect(desktopCandidate.get('.release-cell strong').text()).toBe(
      'Test Series 2026 S01E03 1080p WEB-DL',
    )
    expect(desktopCandidate.get('.reason-list').text()).toContain('EPISODE_COVERAGE_EXACT')
    expect(desktopCandidate.get('.warning-list').text()).toContain('HNR_UNKNOWN')

    const mobileCandidates = wrapper.findAll('.torrent-candidate-mobile-item')
    expect(mobileCandidates).toHaveLength(1)
    expect(mobileCandidates[0].get('.site-id-badge').text()).toBe(
      'Synthetic Two (synthetic-two)',
    )
    expect(mobileCandidates[0].get('h2').text()).toBe(
      'Test Series 2026 S01E03 1080p WEB-DL',
    )
    const downloadButton = desktopCandidate.get('.candidate-download-button')
    expect(downloadButton.text()).toBe('确认下载')
    expect(mocks.approvalConfirmDownload).not.toHaveBeenCalled()
    await downloadButton.trigger('click')
    expect(wrapper.get('[role="dialog"]').text()).toContain(
      'Test Series 2026 S01E03 1080p WEB-DL',
    )
    expect(mocks.approvalConfirmDownload).not.toHaveBeenCalled()
    await wrapper.get('[data-testid="confirm-candidate-download"]').trigger('click')
    await flushPromises()
    expect(mocks.approvalConfirmDownload).toHaveBeenCalledWith(
      'candidate-1',
      'START_IMMEDIATELY',
      expect.stringMatching(/^unin-[0-9a-f]{48}$/),
      expect.any(AbortSignal),
    )
    expect(wrapper.get('[role="dialog"]').text()).toContain('已加入下载队列')
    expect(wrapper.get('a[href="/download-jobs"]').text()).toBe('查看下载任务')

    await wrapper.get('select[aria-label="PT 站点"]').setValue('fixture-nexus')
    expect(wrapper.get('.pt-site-unavailable').text()).toBe('该站点 Profile 尚未启用')
    expect(wrapper.get('.search-preferences button[type="submit"]').attributes('disabled')).toBeDefined()

    await wrapper.get('.header-actions .secondary:nth-child(2)').trigger('click')
    await flushPromises()
    expect(mocks.torrentList).toHaveBeenCalledTimes(2)
    expect(mocks.torrentGet).toHaveBeenCalledTimes(2)
    expect(mocks.torrentCandidates).toHaveBeenCalledTimes(2)
  })

  it('validates and submits editable read-only search preferences', async () => {
    const searchRun = {
      id: 'search-old',
      media_id: 'media-1',
      site_id: 'avistaz',
      status: 'TORRENT_REVIEW',
      strategy_log: [],
      sanitized_request: {},
      candidate_count: 0,
      error_code: null,
      error_message: null,
      started_at: '2026-08-10T00:00:00Z',
      finished_at: '2026-08-10T00:01:00Z',
      created_at: '2026-08-10T00:00:00Z',
    }
    const createdRun = { ...searchRun, id: 'search-new', site_id: 'synthetic-two' }
    mocks.ptSiteCatalog.mockResolvedValueOnce({
      ...ptSiteCatalog,
      sites: [...ptSiteCatalog.sites, syntheticTwoSite],
    })
    mocks.mediaGet.mockResolvedValue(mediaItem)
    mocks.torrentList.mockResolvedValue([searchRun])
    mocks.torrentGet
      .mockResolvedValueOnce(searchRun)
      .mockResolvedValueOnce(createdRun)
    mocks.torrentCandidates
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
    const acceptedRun = {
      ...createdRun,
      job_id: 'job-1',
      deduplicated: false,
    }
    let resolveCreate: (value: typeof acceptedRun) => void = () => undefined
    mocks.torrentCreate.mockReturnValue(new Promise((resolve) => {
      resolveCreate = resolve
    }))

    const wrapper = mount(TorrentCandidatesView)
    await flushPromises()

    await wrapper.get('[data-testid="max-size-gib"]').setValue('-1')
    expect(wrapper.get('.preference-error').text()).toContain('最大体积必须大于 0')
    expect(wrapper.get('.search-preferences button[type="submit"]').attributes('disabled')).toBeDefined()

    await wrapper.get('input[type="checkbox"][value="2160p"]').setValue(false)
    await wrapper.get('input[type="checkbox"][value="BluRay"]').setValue(false)
    await wrapper.get('select[aria-label="PT 站点"]').setValue('synthetic-two')
    await wrapper.get('[data-testid="preferred-audio"]').setValue('Japanese, English')
    await wrapper.get('[data-testid="preferred-subtitles"]').setValue('Chinese')
    await wrapper.get('[data-testid="max-size-gib"]').setValue('5')
    await wrapper.get('.search-preferences').trigger('submit')
    await nextTick()

    expect(wrapper.get('.result-toolbar select').attributes('disabled')).toBeDefined()

    resolveCreate(acceptedRun)
    await flushPromises()

    expect(mocks.torrentCreate).toHaveBeenCalledWith('media-1', {
      site_id: 'synthetic-two',
      preferred_resolutions: ['1080p'],
      preferred_sources: ['WEB-DL'],
      preferred_audio: ['Japanese', 'English'],
      preferred_subtitles: ['Chinese'],
      max_size_bytes: 5_368_709_120,
    })
    expect(wrapper.get('.torrent-candidates-page > .notice-state').text()).toBe(
      'Synthetic Two 搜索完成，找到 0 个候选。',
    )
    const mediaSummary = wrapper.get('.result-toolbar > div')
    expect(mediaSummary.get('strong').text()).toBe('测试剧集')
    expect(mediaSummary.get('small').text()).toBe(
      'TMDB 42 · 国家 / 地区 日本 / 美国 · 地区分组 欧美 / 日本 · Synthetic Two (synthetic-two)',
    )
    const runSelector = wrapper.get('.result-toolbar select')
    expect((runSelector.element as HTMLSelectElement).value).toBe('search-new')
    expect(runSelector.get('option[value="search-new"]').text()).toMatch(
      /^Synthetic Two \(synthetic-two\) · .+ · TORRENT_REVIEW · 0 项$/,
    )
  })
})

describe('QbittorrentView', () => {
  it('shows downloader status and tasks through read-only APIs', async () => {
    mocks.qbStatus.mockResolvedValue({
      connected: true,
      application_version: 'v5.0.4',
      web_api_version: '2.11.4',
      torrent_count: 1,
      category_count: 2,
      active_seeding_count: 1,
    })
    mocks.qbTorrents.mockResolvedValue({
      total: 1,
      items: [
        {
          hash: 'a'.repeat(40),
          name: 'Test Series S01E03',
          size: 5_368_709_120,
          progress: 0.75,
          ratio: 1.25,
          state: 'downloading',
          added_on: 1_786_291_200,
          completion_on: 0,
          seeding_time: 0,
          uploaded: 1_073_741_824,
          upspeed: 1_048_576,
          category: 'media',
          tags: 'avistaz',
          save_path: '/downloads/media',
        },
      ],
    })

    const wrapper = mount(QbittorrentView)
    await flushPromises()

    expect(wrapper.text()).toContain('此页面只调用状态与任务查询接口')
    expect(wrapper.text()).toContain('v5.0.4')
    expect(wrapper.text()).toContain('Test Series S01E03')
    expect(wrapper.text()).toContain('75.0%')
    expect(wrapper.text()).toContain('/downloads/media')

    await wrapper.get('.page-header button').trigger('click')
    await flushPromises()
    expect(mocks.qbStatus).toHaveBeenCalledTimes(2)
    expect(mocks.qbTorrents).toHaveBeenCalledTimes(2)
  })
})

describe('Approval views', () => {
  beforeEach(() => {
    routeState.params = { id: 'approval-1' }
  })

  it('returns a candidate deep link to PT results and canonicalizes a new approval URL', async () => {
    routeState.params = {
      mediaId: 'media-1',
      searchId: 'search-1',
      candidateId: 'candidate-1',
    }
    mocks.mediaGet.mockResolvedValueOnce(mediaItem)
    mocks.torrentCandidates.mockResolvedValueOnce([torrentCandidateResult])
    mocks.approvalList.mockResolvedValueOnce([])
    mocks.approvalCreate.mockResolvedValueOnce(approval)

    const wrapper = mount(ApprovalView)
    await flushPromises()

    const returnLink = wrapper.get('.page-header .button.secondary')
    expect(returnLink.text()).toBe('返回 PT 候选')
    expect(returnLink.attributes('href')).toBe('/media/media-1/torrents')

    const createButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('创建固定快照审批'))
    await createButton?.trigger('click')
    await flushPromises()

    expect(mocks.approvalCreate).toHaveBeenCalledWith('candidate-1', 60)
    expect(routerMock.replace).toHaveBeenCalledWith('/approvals/approval-1')
  })

  it('loads an existing execution from the candidate approval deep link', async () => {
    mockApprovedApprovalRoute('candidate')
    const auth = useAuthStore()
    auth.principal = { username: 'viewer-user', role: 'viewer' }
    mocks.executionForApproval.mockResolvedValueOnce(execution)

    const wrapper = mount(ApprovalView)
    await flushPromises()

    expect(mocks.torrentCandidates).toHaveBeenCalledWith('search-1')
    expect(mocks.approvalList).toHaveBeenCalledWith('candidate-1')
    expect(mocks.executionForApproval).toHaveBeenCalledWith('approval-1')
    expect(wrapper.text()).toContain('已存在下载执行记录，不会重复创建')
    expect(wrapper.find('a[href="/executions/execution-1"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('确认添加到 qB')
    expect(wrapper.text()).not.toContain('确认并立即下载')
    expect(mocks.executionCreateIntent).not.toHaveBeenCalled()
  })

  it.each([
    ['审批详情', 'detail'],
    ['候选审批深链', 'candidate'],
  ] as const)('keeps the manual flow closed while the %s execution lookup is pending', async (_label, routeKind) => {
    mockApprovedApprovalRoute(routeKind)
    let resolveLookup: (value: typeof execution) => void = () => undefined
    mocks.executionForApproval.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveLookup = resolve
      }),
    )

    const wrapper = mount(ApprovalView)
    await flushPromises()

    expect(mocks.executionForApproval).toHaveBeenCalledWith('approval-1')
    expect(wrapper.get('.execution-lookup-guard').text()).toContain('正在确认')
    expect(wrapper.text()).not.toContain('确认添加到 qB')
    expect(wrapper.text()).not.toContain('确认并立即下载')

    resolveLookup(execution)
    await flushPromises()
    expect(wrapper.text()).toContain('已存在下载执行记录，不会重复创建')
  })

  it.each([
    ['审批详情', 'detail'],
    ['候选审批深链', 'candidate'],
  ] as const)('opens the %s manual flow only for DOWNLOAD_EXECUTION_NOT_FOUND', async (_label, routeKind) => {
    mockApprovedApprovalRoute(routeKind)

    const wrapper = mount(ApprovalView)
    await flushPromises()

    expect(mocks.executionForApproval).toHaveBeenCalledWith('approval-1')
    expect(wrapper.text()).not.toContain('审批尚未创建下载执行记录')
    expect(wrapper.find('.execution-lookup-guard').exists()).toBe(false)
    expect(wrapper.text()).toContain('确认添加到 qB')
    expect(wrapper.text()).not.toContain('创建执行意图')
    expect(wrapper.text()).not.toContain('第二步')
    expect(wrapper.find('.execution-control-section input[type="checkbox"]').exists()).toBe(false)
  })

  it.each([
    ['审批详情', 'detail', 404, 'APPROVAL_REQUEST_NOT_FOUND', '审批请求不存在'],
    ['候选审批深链', 'candidate', 404, 'APPROVAL_REQUEST_NOT_FOUND', '审批请求不存在'],
    ['审批详情', 'detail', 500, 'INTERNAL_ERROR', '执行查询暂时失败'],
    ['候选审批深链', 'candidate', 500, 'INTERNAL_ERROR', '执行查询暂时失败'],
  ] as const)(
    'fails the %s manual flow closed for HTTP %s %s',
    async (_label, routeKind, status, errorCode, message) => {
      mockApprovedApprovalRoute(routeKind)
      mocks.executionForApproval.mockRejectedValueOnce(
        Object.assign(new Error(message), { status, errorCode }),
      )

      const wrapper = mount(ApprovalView)
      await flushPromises()

      expect(mocks.executionForApproval).toHaveBeenCalledWith('approval-1')
      expect(wrapper.text()).toContain(message)
      expect(wrapper.get('.execution-lookup-guard').text()).toContain('安全禁用')
      expect(wrapper.text()).not.toContain('确认添加到 qB')
      expect(wrapper.text()).not.toContain('确认并立即下载')
      expect(mocks.executionCreateIntent).not.toHaveBeenCalled()
    },
  )

  it('lists immutable approvals and their preflight state', async () => {
    mocks.approvalList.mockResolvedValueOnce([approval])
    const wrapper = mount(ApprovalListView)
    await flushPromises()
    expect(wrapper.text()).toContain('测试剧集')
    expect(wrapper.text()).toContain('UNKNOWN')
    expect(wrapper.text()).toContain('查看审批')
    expect(wrapper.findAll('.approval-mobile-item')).toHaveLength(1)
    expect(wrapper.get('.approval-mobile-item').text()).toContain('测试剧集')
    expect(wrapper.get('.approval-mobile-item a').attributes('href')).toBe('/approvals/approval-1')
    expect(wrapper.get('.phase-banner').text()).toContain('审批只生成不可执行计划')
    expect(wrapper.get('.phase-banner').text()).toContain('管理员一次确认下载执行')
    expect(wrapper.get('.phase-banner').text()).toContain('自动执行策略')
    expect(wrapper.get('.phase-banner').text()).toContain('服务端执行总闸开启')
  })

  it('runs a fresh preflight from approve and keeps blocked approvals pending', async () => {
    mocks.approvalGet.mockResolvedValueOnce({
      ...approval,
      candidate: { ...approval.candidate, site_id: 'synthetic-two' },
    })
    mocks.approvalApprove.mockResolvedValueOnce({
      ...approval,
      candidate: { ...approval.candidate, site_id: 'synthetic-two' },
      status: 'PENDING',
      preflight_result: {
        ...approval.preflight_result,
        overall_status: 'BLOCKED',
        checked_at: '2026-08-10T00:12:00Z',
        checks: [
          {
            code: 'DUPLICATE_INFO_HASH',
            status: 'BLOCKED',
            message: 'qBittorrent 已存在相同 info_hash',
            details: {},
          },
        ],
      },
      preflight_checked_at: '2026-08-10T00:12:00Z',
    })
    const wrapper = mount(ApprovalView)
    await flushPromises()
    expect(wrapper.text()).toContain('候选 H&R 规则未知')
    expect(wrapper.get('.page > .phase-banner').text()).toContain('审批只生成不可执行计划')
    expect(wrapper.get('.page > .phase-banner').text()).toContain('管理员一次确认下载执行')
    expect(wrapper.get('.page > .phase-banner').text()).toContain('自动执行策略')
    expect(wrapper.get('.page > .phase-banner').text()).toContain('服务端执行总闸开启')
    expect(wrapper.get('.acknowledgements').text()).toContain('受控下载执行可能随即排队')
    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    for (const checkbox of checkboxes) await checkbox.setValue(true)
    const approveButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('预检并批准计划'))
    expect(approveButton?.attributes('disabled')).toBeUndefined()
    await approveButton?.trigger('click')
    await flushPromises()

    expect(mocks.approvalPreflight).not.toHaveBeenCalled()
    expect(mocks.approvalApprove).toHaveBeenCalledOnce()
    expect(mocks.executionForApproval).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('最新预检未通过，审批仍为待处理')
    expect(wrapper.text()).toContain('qBittorrent 已存在相同 info_hash')
    expect(wrapper.text()).toContain('已阻止')
  })

  it('uses the AvistaZ 7-day H&R default without an acknowledgement', async () => {
    mocks.approvalGet.mockResolvedValueOnce({
      ...approval,
      candidate: {
        ...approval.candidate,
        hit_and_run: null,
        warnings: ['HNR_UNKNOWN', 'TRACKER_POLICY_NOT_VERIFIED'],
      },
      preflight_result: {
        ...approval.preflight_result,
        overall_status: 'PASS',
        checks: [{ code: 'READY', status: 'PASS', message: '预检通过', details: {} }],
      },
    })
    mocks.approvalApprove.mockResolvedValueOnce({ ...approval, status: 'APPROVED' })
    mocks.approvalPlan.mockResolvedValueOnce(downloadPlan)
    const wrapper = mount(ApprovalView)
    await flushPromises()

    expect(wrapper.text()).toContain('站点默认 7 天')
    expect(wrapper.get('.acknowledgements').text()).not.toContain('已了解该站 H&R 规则')
    expect(wrapper.get('.warning-list').text()).not.toContain('HNR_UNKNOWN')
    expect(wrapper.get('.warning-list').text()).toContain('TRACKER_POLICY_NOT_VERIFIED')

    const acknowledgements = wrapper.findAll('.acknowledgements input[type="checkbox"]')
    expect(acknowledgements).toHaveLength(2)
    for (const checkbox of acknowledgements) await checkbox.setValue(true)
    const approveButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('预检并批准计划'))
    expect(approveButton?.attributes('disabled')).toBeUndefined()
    await approveButton?.trigger('click')
    await flushPromises()

    expect(mocks.approvalApprove).toHaveBeenCalledWith('approval-1', {
      acknowledges_hnr: false,
      acknowledges_seeding: true,
      acknowledges_plan_only: true,
    })
  })

  it('keeps H&R acknowledgement and warnings for other PT sites', async () => {
    mocks.approvalGet.mockResolvedValueOnce({
      ...approval,
      candidate: {
        ...approval.candidate,
        site_id: 'synthetic-two',
        hit_and_run: null,
        warnings: ['HNR_UNKNOWN', 'TRACKER_POLICY_NOT_VERIFIED'],
      },
      preflight_result: {
        ...approval.preflight_result,
        overall_status: 'PASS',
        checks: [{ code: 'READY', status: 'PASS', message: '预检通过', details: {} }],
      },
    })
    mocks.approvalApprove.mockResolvedValueOnce({ ...approval, status: 'APPROVED' })
    mocks.approvalPlan.mockResolvedValueOnce(downloadPlan)
    const wrapper = mount(ApprovalView)
    await flushPromises()

    expect(wrapper.text()).not.toContain('站点默认 7 天')
    expect(wrapper.get('.acknowledgements').text()).toContain('已了解该站 H&R 规则')
    expect(wrapper.get('.warning-list').text()).toContain('HNR_UNKNOWN')
    expect(wrapper.get('.warning-list').text()).toContain('TRACKER_POLICY_NOT_VERIFIED')

    const acknowledgementLabels = wrapper.findAll('.acknowledgements label')
    await acknowledgementLabels
      .find((label) => label.text().includes('继续做种'))
      ?.get('input')
      .setValue(true)
    await acknowledgementLabels
      .find((label) => label.text().includes('审批本身只生成'))
      ?.get('input')
      .setValue(true)
    const approveButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('预检并批准计划'))
    expect(approveButton?.attributes('disabled')).toBeDefined()

    await acknowledgementLabels
      .find((label) => label.text().includes('H&R'))
      ?.get('input')
      .setValue(true)
    expect(approveButton?.attributes('disabled')).toBeUndefined()
    await approveButton?.trigger('click')
    await flushPromises()

    expect(mocks.approvalApprove).toHaveBeenCalledWith('approval-1', {
      acknowledges_hnr: true,
      acknowledges_seeding: true,
      acknowledges_plan_only: true,
    })
  })

  it('reacts to role changes for operator and admin approval controls', async () => {
    mocks.approvalGet.mockResolvedValueOnce({
      ...approval,
      preflight_result: {
        ...approval.preflight_result,
        overall_status: 'PASS',
        checks: [
          {
            code: 'READY',
            status: 'PASS',
            message: '预检通过',
            details: {},
          },
        ],
      },
    })
    const wrapper = mount(ApprovalView)
    await flushPromises()
    const auth = useAuthStore()
    const findButton = (text: string) => wrapper.findAll('button').find((item) => item.text().includes(text))

    for (const checkbox of wrapper.findAll('input[type="checkbox"]')) await checkbox.setValue(true)
    expect(findButton('预检并批准计划')?.attributes('disabled')).toBeUndefined()
    expect(findButton('拒绝')?.attributes('disabled')).toBeUndefined()
    expect(findButton('运行 qB 只读预检')).toBeUndefined()

    auth.principal = { username: 'operator-user', role: 'operator' }
    await nextTick()
    expect(findButton('预检并批准计划')?.attributes('disabled')).toBeDefined()
    expect(findButton('拒绝')?.attributes('disabled')).toBeUndefined()

    auth.principal = { username: 'viewer-user', role: 'viewer' }
    await nextTick()
    expect(findButton('预检并批准计划')?.attributes('disabled')).toBeDefined()
    expect(findButton('拒绝')?.attributes('disabled')).toBeDefined()
  })

  it('uses a private one-time intent behind the single paused execution action', async () => {
    mocks.approvalGet.mockResolvedValueOnce({ ...approval, status: 'APPROVED' })
    mocks.approvalPlan.mockResolvedValueOnce(downloadPlan)
    mocks.executionCreateIntent.mockResolvedValueOnce(executionIntent)
    mocks.executionExecute.mockResolvedValueOnce(execution)
    const wrapper = mount(ApprovalView)
    await flushPromises()
    const control = wrapper.get('.execution-control-section')

    expect(wrapper.get('.download-plan .phase-banner').text()).toContain(
      '选择启动模式后，只需确认一次即可提交下载执行',
    )
    expect(control.get('.segmented-control button.active').text()).toContain('添加后暂停')
    expect(control.find('input[type="checkbox"]').exists()).toBe(false)
    expect(control.text()).not.toContain('创建执行意图')
    expect(control.text()).not.toContain('第二步')

    const submitButton = control
      .findAll('button')
      .find((item) => item.text().includes('确认添加到 qB'))
    expect(submitButton?.attributes('disabled')).toBeUndefined()
    await submitButton?.trigger('click')
    await flushPromises()

    expect(mocks.executionCreateIntent).toHaveBeenCalledWith('approval-1', 'ADD_PAUSED')
    expect(wrapper.text()).not.toContain(`ei1_${'n'.repeat(32)}`)
    expect(mocks.executionExecute).toHaveBeenCalledWith(
      'approval-1',
      'intent-1',
      `ei1_${'n'.repeat(32)}`,
      expect.stringMatching(/^unin-[0-9a-f]{48}$/),
    )
    const createCallOrder = mocks.executionCreateIntent.mock.invocationCallOrder[0]
    const executeCallOrder = mocks.executionExecute.mock.invocationCallOrder[0]
    expect(createCallOrder).toBeDefined()
    expect(executeCallOrder).toBeDefined()
    expect(createCallOrder!).toBeLessThan(executeCallOrder!)
    expect(wrapper.text()).toContain('执行记录已创建')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('keeps execution closed after an uncertain execute response', async () => {
    mocks.approvalGet.mockResolvedValueOnce({ ...approval, status: 'APPROVED' })
    mocks.approvalPlan.mockResolvedValueOnce(downloadPlan)
    mocks.executionCreateIntent.mockResolvedValueOnce(executionIntent)
    mocks.executionExecute.mockRejectedValueOnce(new Error('网络响应未知'))
    const wrapper = mount(ApprovalView)
    await flushPromises()
    const control = wrapper.get('.execution-control-section')

    await control
      .findAll('button')
      .find((item) => item.text().includes('确认添加到 qB'))
      ?.trigger('click')
    await flushPromises()

    expect(mocks.executionCreateIntent).toHaveBeenCalledOnce()
    expect(mocks.executionExecute).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('网络响应未知')
    expect(wrapper.get('.execution-lookup-guard').text()).toContain('安全禁用')
    expect(wrapper.text()).not.toContain('确认添加到 qB')
    expect(wrapper.text()).not.toContain('确认并立即下载')
    expect(wrapper.text()).not.toContain('尚未提交')
  })

  it('creates and executes an immediate-start intent from one explicit action', async () => {
    mocks.approvalGet.mockResolvedValueOnce({ ...approval, status: 'APPROVED' })
    mocks.approvalPlan.mockResolvedValueOnce(downloadPlan)
    mocks.executionCreateIntent.mockResolvedValueOnce({
      ...executionIntent,
      launch_mode: 'START_IMMEDIATELY',
    })
    mocks.executionExecute.mockResolvedValueOnce({
      ...execution,
      launch_mode: 'START_IMMEDIATELY',
    })
    const wrapper = mount(ApprovalView)
    await flushPromises()
    const control = wrapper.get('.execution-control-section')
    const immediateButton = control.findAll('.segmented-control button').find((item) => item.text().includes('立即开始'))
    await immediateButton?.trigger('click')
    expect(control.find('input[type="checkbox"]').exists()).toBe(false)
    const submitButton = control
      .findAll('button')
      .find((item) => item.text().includes('确认并立即下载'))
    expect(submitButton?.attributes('disabled')).toBeUndefined()

    await submitButton?.trigger('click')
    await flushPromises()

    expect(mocks.executionCreateIntent).toHaveBeenCalledWith(
      'approval-1',
      'START_IMMEDIATELY',
    )
    expect(mocks.executionExecute).toHaveBeenCalledWith(
      'approval-1',
      'intent-1',
      `ei1_${'n'.repeat(32)}`,
      expect.stringMatching(/^unin-[0-9a-f]{48}$/),
    )
  })

  it('shows a disabled execution backend error without claiming success', async () => {
    mocks.approvalGet.mockResolvedValueOnce({ ...approval, status: 'APPROVED' })
    mocks.approvalPlan.mockResolvedValueOnce(downloadPlan)
    mocks.executionCreateIntent.mockRejectedValueOnce(new Error('下载执行控制默认关闭'))
    const wrapper = mount(ApprovalView)
    await flushPromises()
    const control = wrapper.get('.execution-control-section')
    await control
      .findAll('button')
      .find((item) => item.text().includes('确认添加到 qB'))
      ?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('下载执行控制默认关闭')
    expect(wrapper.text()).not.toContain('执行记录已创建')
    expect(mocks.executionExecute).not.toHaveBeenCalled()
  })

  it.each([
    ['APPROVED', 'PENDING', null],
    ['APPROVED', 'FAILED', '执行器校验失败'],
    ['APPROVED', 'CANCELLED', null],
    ['EXECUTING', 'RECONCILIATION_PENDING', '等待人工对账'],
  ] as const)(
    'shows an existing %s approval execution in %s without offering a duplicate intent',
    async (approvalStatus, executionStatus, errorMessage) => {
      mocks.approvalGet.mockResolvedValueOnce({ ...approval, status: approvalStatus })
      mocks.approvalPlan.mockResolvedValueOnce(downloadPlan)
      mocks.executionForApproval.mockResolvedValueOnce({
        ...execution,
        status: executionStatus,
        requires_reconciliation: executionStatus === 'RECONCILIATION_PENDING',
        error_code: errorMessage ? 'EXECUTION_REQUIRES_ATTENTION' : null,
        error_message: errorMessage,
      })
      const wrapper = mount(ApprovalView)
      await flushPromises()

      expect(mocks.executionForApproval).toHaveBeenCalledWith('approval-1')
      expect(wrapper.text()).toContain('已存在下载执行记录，不会重复创建')
      if (errorMessage) expect(wrapper.text()).toContain(errorMessage)
      expect(wrapper.find('a[href="/executions/execution-1"]').exists()).toBe(true)
      expect(wrapper.text()).not.toContain('确认添加到 qB')
      expect(wrapper.text()).not.toContain('确认并立即下载')
      expect(mocks.executionCreateIntent).not.toHaveBeenCalled()
    },
  )

})
