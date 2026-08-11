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
import SystemView from '../src/views/SystemView.vue'
import TorrentCandidatesView from '../src/views/TorrentCandidatesView.vue'
import { useAuthStore } from '../src/stores/auth'
import { useMediaStore } from '../src/stores/media'

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
  torrentList: vi.fn(),
  torrentGet: vi.fn(),
  torrentCandidates: vi.fn(),
  torrentCreate: vi.fn(),
  approvalList: vi.fn(),
  approvalGet: vi.fn(),
  approvalCreate: vi.fn(),
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
  executionReconcile: vi.fn(),
  qbStatus: vi.fn(),
  qbTorrents: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'media-1' } }),
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
  torrentApi: {
    list: mocks.torrentList,
    get: mocks.torrentGet,
    candidates: mocks.torrentCandidates,
    create: mocks.torrentCreate,
  },
  approvalApi: {
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

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  const auth = useAuthStore()
  auth.principal = { username: 'admin-user', role: 'admin' }
  auth.initialized = true
})

describe('MediaView', () => {
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

describe('SystemView', () => {
  it('shows component health without persisting credentials', async () => {
    mocks.systemStatus.mockResolvedValueOnce({
      api: { healthy: true, message: 'API 运行正常', checked_at: '2026-08-10T00:00:00Z' },
      worker: { healthy: true, message: 'Worker 运行正常', checked_at: '2026-08-10T00:00:00Z' },
      postgres: {
        healthy: true,
        message: 'PostgreSQL 连接正常',
        checked_at: '2026-08-10T00:00:00Z',
      },
      nextfind_configured: true,
      tmdb_configured: false,
      tmdb_live_enabled: false,
      avistaz_configured: false,
      avistaz_live_enabled: false,
      avistaz_status: '本阶段未启用',
    })
    const wrapper = mount(SystemView)
    await flushPromises()
    expect(wrapper.text()).toContain('PostgreSQL')
    expect(wrapper.text()).toContain('已配置')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })
})

describe('IdentityView', () => {
  it('shows source data, TMDB candidates, conflicts and manual confirmation', async () => {
    mocks.mediaGet.mockResolvedValue(mediaItem)
    mocks.metadataCandidates.mockResolvedValue([
      {
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
          aliases: [],
          year: 2026,
          number_of_seasons: 1,
          number_of_episodes: 8,
          episode_matrix: { 1: [1, 2, 3] },
          poster_path: '/poster.jpg',
          backdrop_path: null,
          status: 'Returning Series',
          confidence: 1,
          external_ids: { imdb_id: 'tt0042' },
        },
      },
    ])
    mocks.confirmIdentity.mockResolvedValue({ status: 'CONFIRMED' })
    const wrapper = mount(IdentityView)
    await flushPromises()
    expect(wrapper.text()).toContain('刷新候选')
    expect(wrapper.text()).toContain('NextFind 标题')
    expect(wrapper.text()).toContain('Test Series')
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
  it('shows read-only scored candidates without a download action', async () => {
    const searchRun = {
      id: 'search-1',
      media_id: 'media-1',
      site_id: 'avistaz',
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
          site_id: 'avistaz',
          torrent_id: 'torrent-1',
          release_title: 'Test Series 2026 S01E03 1080p WEB-DL',
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
    const wrapper = mount(TorrentCandidatesView)
    await flushPromises()
    expect(wrapper.text()).toContain('刷新结果')
    expect(wrapper.text()).toContain('当前阶段仅支持只读搜索')
    expect(wrapper.text()).toContain('Test Series 2026 S01E03')
    expect(wrapper.text()).toContain('EPISODE_COVERAGE_EXACT')
    expect(wrapper.text()).toContain('HNR_UNKNOWN')
    expect(wrapper.text()).not.toContain('下载种子')
    expect(wrapper.find('a[href*="download"]').exists()).toBe(false)

    await wrapper.get('.header-actions .secondary:nth-child(2)').trigger('click')
    await flushPromises()
    expect(mocks.torrentList).toHaveBeenCalledTimes(2)
    expect(mocks.torrentGet).toHaveBeenCalledTimes(2)
    expect(mocks.torrentCandidates).toHaveBeenCalledTimes(2)
  })

  it('validates and submits editable read-only search preferences', async () => {
    const searchRun = {
      id: 'search-2',
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
    mocks.mediaGet.mockResolvedValue(mediaItem)
    mocks.torrentList.mockResolvedValue([searchRun])
    mocks.torrentGet.mockResolvedValue(searchRun)
    mocks.torrentCandidates.mockResolvedValue([])
    mocks.torrentCreate.mockResolvedValue({ ...searchRun, job_id: 'job-1', deduplicated: false })

    const wrapper = mount(TorrentCandidatesView)
    await flushPromises()

    await wrapper.get('[data-testid="max-size-gib"]').setValue('-1')
    expect(wrapper.text()).toContain('最大体积必须大于 0')
    expect(wrapper.get('.search-preferences button[type="submit"]').attributes('disabled')).toBeDefined()

    await wrapper.get('input[type="checkbox"][value="2160p"]').setValue(false)
    await wrapper.get('input[type="checkbox"][value="BluRay"]').setValue(false)
    await wrapper.get('[data-testid="preferred-audio"]').setValue('Japanese, English')
    await wrapper.get('[data-testid="preferred-subtitles"]').setValue('Chinese')
    await wrapper.get('[data-testid="max-size-gib"]').setValue('5')
    await wrapper.get('.search-preferences').trigger('submit')
    await flushPromises()

    expect(mocks.torrentCreate).toHaveBeenCalledWith('media-1', {
      preferred_resolutions: ['1080p'],
      preferred_sources: ['WEB-DL'],
      preferred_audio: ['Japanese', 'English'],
      preferred_subtitles: ['Chinese'],
      max_size_bytes: 5_368_709_120,
    })
    expect(wrapper.text()).toContain('任务异步执行')
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
  it('lists immutable approvals and their preflight state', async () => {
    mocks.approvalList.mockResolvedValueOnce([approval])
    const wrapper = mount(ApprovalListView)
    await flushPromises()
    expect(wrapper.text()).toContain('测试剧集')
    expect(wrapper.text()).toContain('UNKNOWN')
    expect(wrapper.text()).toContain('查看审批')
  })

  it('does not enable approval when preflight is UNKNOWN', async () => {
    mocks.approvalGet.mockResolvedValueOnce(approval)
    const wrapper = mount(ApprovalView)
    await flushPromises()
    expect(wrapper.text()).toContain('候选 H&R 规则未知')
    expect(wrapper.text()).toContain('仅管理员可在已批准计划上完成两步确认')
    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    for (const checkbox of checkboxes) await checkbox.setValue(true)
    const approveButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('批准并生成计划'))
    expect(approveButton?.attributes('disabled')).toBeDefined()
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
    expect(findButton('批准并生成计划')?.attributes('disabled')).toBeUndefined()
    expect(findButton('拒绝')?.attributes('disabled')).toBeUndefined()

    auth.principal = { username: 'operator-user', role: 'operator' }
    await nextTick()
    expect(findButton('批准并生成计划')?.attributes('disabled')).toBeDefined()
    expect(findButton('运行 qB 只读预检')?.attributes('disabled')).toBeUndefined()
    expect(findButton('拒绝')?.attributes('disabled')).toBeUndefined()

    auth.principal = { username: 'viewer-user', role: 'viewer' }
    await nextTick()
    expect(findButton('运行 qB 只读预检')?.attributes('disabled')).toBeDefined()
    expect(findButton('拒绝')?.attributes('disabled')).toBeDefined()
  })

  it('uses a private one-time intent for the two-step paused execution flow', async () => {
    mocks.approvalGet.mockResolvedValueOnce({ ...approval, status: 'APPROVED' })
    mocks.approvalPlan.mockResolvedValueOnce(downloadPlan)
    mocks.executionCreateIntent.mockResolvedValueOnce({
      id: 'intent-1',
      approval_id: 'approval-1',
      nonce: `ei1_${'n'.repeat(32)}`,
      status: 'ACTIVE',
      approval_snapshot_hash: 'a'.repeat(64),
      plan_hash: 'c'.repeat(64),
      qb_target_fingerprint: 'd'.repeat(64),
      launch_mode: 'ADD_PAUSED',
      expires_at: '2026-08-10T00:20:00Z',
      created_at: '2026-08-10T00:12:00Z',
    })
    mocks.executionExecute.mockResolvedValueOnce(execution)
    const wrapper = mount(ApprovalView)
    await flushPromises()
    const control = wrapper.get('.execution-control-section')

    expect(control.get('.segmented-control button.active').text()).toContain('添加后暂停')
    await control.get('.execution-checks input[type="checkbox"]').setValue(true)
    await control.findAll('button').find((item) => item.text().includes('第一步'))?.trigger('click')
    await flushPromises()

    expect(mocks.executionCreateIntent).toHaveBeenCalledWith('approval-1', 'ADD_PAUSED')
    expect(wrapper.text()).not.toContain(`ei1_${'n'.repeat(32)}`)
    await control.get('.final-step input[type="checkbox"]').setValue(true)
    await control.findAll('button').find((item) => item.text().includes('第二步'))?.trigger('click')
    await flushPromises()

    expect(mocks.executionExecute).toHaveBeenCalledWith(
      'approval-1',
      'intent-1',
      `ei1_${'n'.repeat(32)}`,
      expect.stringMatching(/^unin-[0-9a-f]{48}$/),
    )
    expect(wrapper.text()).toContain('执行记录已创建')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('requires an extra explicit confirmation for immediate start', async () => {
    mocks.approvalGet.mockResolvedValueOnce({ ...approval, status: 'APPROVED' })
    mocks.approvalPlan.mockResolvedValueOnce(downloadPlan)
    const wrapper = mount(ApprovalView)
    await flushPromises()
    const control = wrapper.get('.execution-control-section')
    const immediateButton = control.findAll('.segmented-control button').find((item) => item.text().includes('立即开始'))
    await immediateButton?.trigger('click')
    const checks = control.findAll('.execution-checks input[type="checkbox"]')
    await checks[0]?.setValue(true)
    const firstStep = control.findAll('button').find((item) => item.text().includes('第一步'))
    expect(firstStep?.attributes('disabled')).toBeDefined()
    await checks[1]?.setValue(true)
    expect(firstStep?.attributes('disabled')).toBeUndefined()
  })

  it('shows a disabled execution backend error without claiming success', async () => {
    mocks.approvalGet.mockResolvedValueOnce({ ...approval, status: 'APPROVED' })
    mocks.approvalPlan.mockResolvedValueOnce(downloadPlan)
    mocks.executionCreateIntent.mockRejectedValueOnce(new Error('下载执行控制默认关闭'))
    const wrapper = mount(ApprovalView)
    await flushPromises()
    const control = wrapper.get('.execution-control-section')
    await control.get('.execution-checks input[type="checkbox"]').setValue(true)
    await control.findAll('button').find((item) => item.text().includes('第一步'))?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('下载执行控制默认关闭')
    expect(wrapper.text()).not.toContain('执行记录已创建')
    expect(mocks.executionExecute).not.toHaveBeenCalled()
  })

  it('resumes an existing execution from an EXECUTING approval without requesting a new intent', async () => {
    mocks.approvalGet.mockResolvedValueOnce({ ...approval, status: 'EXECUTING' })
    mocks.approvalPlan.mockResolvedValueOnce(downloadPlan)
    mocks.executionForApproval.mockResolvedValueOnce({ ...execution, status: 'SUBMITTED' })
    const wrapper = mount(ApprovalView)
    await flushPromises()

    expect(mocks.executionForApproval).toHaveBeenCalledWith('approval-1')
    expect(wrapper.text()).toContain('执行记录已创建，后台结果仍需继续观察')
    expect(wrapper.find('a[href="/executions/execution-1"]').exists()).toBe(true)
    expect(mocks.executionCreateIntent).not.toHaveBeenCalled()
  })
})
