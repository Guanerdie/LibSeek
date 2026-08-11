import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import StatusPill from '../src/components/StatusPill.vue'
import { useApprovalStore } from '../src/stores/approvals'
import { useTorrentStore } from '../src/stores/torrents'
import type {
  MediaItem,
  PtSiteCatalog,
  TorrentCandidateResult,
  TorrentSearchRun,
} from '../src/types'

const mocks = vi.hoisted(() => ({
  approvalApprove: vi.fn(),
  approvalPlan: vi.fn(),
  mediaGet: vi.fn(),
  ptSiteCatalog: vi.fn(),
  torrentList: vi.fn(),
  torrentGet: vi.fn(),
  torrentCandidates: vi.fn(),
  torrentCreate: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  approvalApi: {
    approve: mocks.approvalApprove,
    plan: mocks.approvalPlan,
  },
  mediaApi: { get: mocks.mediaGet },
  ptSiteApi: { catalog: mocks.ptSiteCatalog },
  torrentApi: {
    list: mocks.torrentList,
    get: mocks.torrentGet,
    candidates: mocks.torrentCandidates,
    create: mocks.torrentCreate,
  },
}))

const mediaItem: MediaItem = {
  id: 'media-1',
  source: 'nextfind',
  source_item_id: 'source-1',
  media_type: 'tv',
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
  identity_confidence: 'HIGH',
  metadata_status: 'RESOLVED',
  workflow_status: 'TORRENT_REVIEW',
  discovered_at: '2026-08-10T00:00:00Z',
  updated_at: '2026-08-10T00:00:00Z',
}

function makeCatalog(siteId = 'avistaz'): PtSiteCatalog {
  return {
    default_site_id: siteId,
    sites: [
      {
        site_id: siteId,
        display_name:
          siteId === 'avistaz'
            ? 'AvistaZ'
            : siteId === 'synthetic-two'
              ? 'Synthetic Two'
              : 'Fixture Nexus',
        description: '只读候选搜索',
        available_for_search: true,
        mode: 'LIVE_READ_ONLY_SEARCH',
        search_modes: ['TMDB_ID', 'TEXT'],
        media_types: ['movie', 'tv'],
        manual_only: siteId !== 'avistaz',
        promotion_metadata: true,
        hit_and_run_metadata: false,
        torrent_fetch_enabled: false,
        unavailable_reason_code: null,
        unavailable_reason_message: null,
      },
    ],
  }
}

function makeRun(
  id: string,
  siteId = 'avistaz',
  mediaId = 'media-1',
): TorrentSearchRun {
  return {
    id,
    media_id: mediaId,
    site_id: siteId,
    status: 'TORRENT_REVIEW',
    strategy_log: [],
    sanitized_request: {},
    candidate_count: 1,
    error_code: null,
    error_message: null,
    started_at: '2026-08-10T00:00:00Z',
    finished_at: '2026-08-10T00:01:00Z',
    created_at: '2026-08-10T00:00:00Z',
  }
}

function makeCandidate(
  id: string,
  searchId: string,
  siteId = 'avistaz',
): TorrentCandidateResult {
  return {
    id,
    search_run_id: searchId,
    match_score: 0.9,
    match_reasons: [],
    warnings: [],
    created_at: '2026-08-10T00:01:00Z',
    candidate: {
      site_id: siteId,
      torrent_id: `torrent-${id}`,
      release_title: `Release ${id}`,
      details_ref: `${siteId}:details:${id}`,
      media_type: 'tv',
      tmdb_id: 42,
      imdb_id: null,
      year: 2026,
      season: 1,
      episodes: [3],
      collection_type: 'episode',
      resolution: '1080p',
      source: 'WEB-DL',
      codec: 'H.265',
      hdr: null,
      audio: null,
      subtitles: null,
      size_bytes: 1024,
      file_count: 1,
      seeders: 1,
      leechers: 0,
      completed: 1,
      download_factor: 1,
      upload_factor: 1,
      hit_and_run: null,
      info_hash: null,
      published_at: null,
      match_score: 0.9,
      match_reasons: [],
      warnings: [],
    },
  }
}

function deferred<T>(): {
  promise: Promise<T>
  resolve: (value: T) => void
} {
  let resolve: (value: T) => void = () => undefined
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  mocks.mediaGet.mockResolvedValue(mediaItem)
  mocks.ptSiteCatalog.mockResolvedValue(makeCatalog())
  mocks.torrentList.mockResolvedValue([])
})

describe('torrent search selection reliability', () => {
  it('keeps media, run and candidate identities scoped to the second PT site', async () => {
    const secondSiteRun = makeRun('search-two', 'synthetic-two')
    const secondSiteCandidate = makeCandidate(
      'candidate-two',
      'search-two',
      'synthetic-two',
    )
    mocks.ptSiteCatalog.mockResolvedValueOnce(makeCatalog('synthetic-two'))
    mocks.torrentList.mockResolvedValueOnce([secondSiteRun])
    mocks.torrentGet.mockResolvedValueOnce(secondSiteRun)
    mocks.torrentCandidates.mockResolvedValueOnce([secondSiteCandidate])

    const store = useTorrentStore()
    await store.loadCatalog()
    await store.load('media-1')

    expect(store.selectedSiteId).toBe('synthetic-two')
    expect(store.media?.id).toBe('media-1')
    expect(
      store.runs.map((item) => ({
        id: item.id,
        media_id: item.media_id,
        site_id: item.site_id,
      })),
    ).toEqual([{ id: 'search-two', media_id: 'media-1', site_id: 'synthetic-two' }])
    expect(store.selectedRun).toMatchObject({
      id: 'search-two',
      media_id: 'media-1',
      site_id: 'synthetic-two',
    })
    expect(
      store.candidates.map((item) => ({
        id: item.id,
        search_run_id: item.search_run_id,
        site_id: item.candidate.site_id,
      })),
    ).toEqual([
      {
        id: 'candidate-two',
        search_run_id: 'search-two',
        site_id: 'synthetic-two',
      },
    ])
    expect(store.error).toBeNull()
    expect(store.selectionError).toBeNull()
  })

  it('aborts and ignores an older search response after a newer selection', async () => {
    const oldRun = deferred<TorrentSearchRun>()
    const oldCandidates = deferred<TorrentCandidateResult[]>()
    const newRun = deferred<TorrentSearchRun>()
    const newCandidates = deferred<TorrentCandidateResult[]>()

    mocks.torrentGet.mockReturnValueOnce(oldRun.promise).mockReturnValueOnce(newRun.promise)
    mocks.torrentCandidates
      .mockReturnValueOnce(oldCandidates.promise)
      .mockReturnValueOnce(newCandidates.promise)

    const store = useTorrentStore()
    await store.load('media-1')
    store.runs = [makeRun('old-search'), makeRun('new-search')]
    const first = store.selectRun('old-search')
    const oldSignal = mocks.torrentGet.mock.calls[0]?.[1] as AbortSignal
    const second = store.selectRun('new-search')

    expect(oldSignal.aborted).toBe(true)
    newRun.resolve(makeRun('new-search'))
    newCandidates.resolve([makeCandidate('new-candidate', 'new-search')])
    await second

    oldRun.resolve(makeRun('old-search'))
    oldCandidates.resolve([makeCandidate('old-candidate', 'old-search')])
    await first

    expect(store.selectedRun?.id).toBe('new-search')
    expect(store.candidates.map((item) => item.id)).toEqual(['new-candidate'])
    expect(store.selectionError).toBeNull()
    expect(store.selecting).toBe(false)
  })

  it('fails closed when a candidate belongs to another PT site', async () => {
    const store = useTorrentStore()
    await store.load('media-1')
    store.runs = [makeRun('search-1', 'synthetic-two')]
    mocks.torrentGet.mockResolvedValueOnce(makeRun('search-1', 'synthetic-two'))
    mocks.torrentCandidates.mockResolvedValueOnce([
      makeCandidate('candidate-1', 'search-1', 'avistaz'),
    ])

    await store.selectRun('search-1')

    expect(store.selectedRun).toBeNull()
    expect(store.candidates).toEqual([])
    expect(store.selectionError).toContain('响应身份不一致')
  })

  it.each([
    ['run id', makeRun('other-search'), []],
    ['run media', makeRun('search-1', 'avistaz', 'media-2'), []],
    ['run site', makeRun('search-1', 'synthetic-two'), []],
    [
      'candidate run',
      makeRun('search-1'),
      [makeCandidate('candidate-1', 'other-search')],
    ],
  ])('fails closed for a mismatched %s binding', async (_label, run, candidates) => {
    const store = useTorrentStore()
    await store.load('media-1')
    store.runs = [makeRun('search-1')]
    mocks.torrentGet.mockResolvedValueOnce(run)
    mocks.torrentCandidates.mockResolvedValueOnce(candidates)

    await store.selectRun('search-1')

    expect(store.selectedRun).toBeNull()
    expect(store.candidates).toEqual([])
    expect(store.selectionError).toContain('响应身份不一致')
  })

  it('ignores an older catalog response and clears only catalog state on failure', async () => {
    const oldCatalog = deferred<PtSiteCatalog>()
    const newCatalog = deferred<PtSiteCatalog>()
    mocks.ptSiteCatalog
      .mockReturnValueOnce(oldCatalog.promise)
      .mockReturnValueOnce(newCatalog.promise)
    const store = useTorrentStore()
    store.runs = [makeRun('historical-run')]

    const first = store.loadCatalog()
    const second = store.loadCatalog()
    newCatalog.resolve(makeCatalog('fixture-nexus'))
    await second
    oldCatalog.resolve(makeCatalog('avistaz'))
    await first

    expect(store.selectedSiteId).toBe('fixture-nexus')
    expect(store.sites.map((site) => site.site_id)).toEqual(['fixture-nexus'])

    mocks.ptSiteCatalog.mockRejectedValueOnce(new Error('目录暂时不可用'))
    await store.loadCatalog()

    expect(store.sites).toEqual([])
    expect(store.selectedSiteId).toBe('')
    expect(store.catalogError).toContain('目录暂时不可用')
    expect(store.runs.map((run) => run.id)).toEqual(['historical-run'])
  })

  it('does not let a delayed create response reload an older media item', async () => {
    const accepted = deferred<TorrentSearchRun & { job_id: string; deduplicated: boolean }>()
    const store = useTorrentStore()
    await store.loadCatalog()
    await store.load('media-1')
    mocks.torrentCreate.mockReturnValueOnce(accepted.promise)

    const pending = store.create('media-1', {
      site_id: 'avistaz',
      preferred_resolutions: ['1080p'],
      preferred_sources: ['WEB-DL'],
      preferred_audio: [],
      preferred_subtitles: ['Chinese'],
    })
    mocks.mediaGet.mockResolvedValueOnce({ ...mediaItem, id: 'media-2' })
    mocks.torrentList.mockResolvedValueOnce([])
    await store.load('media-2')
    accepted.resolve({
      ...makeRun('search-old', 'avistaz', 'media-1'),
      job_id: 'job-old',
      deduplicated: false,
    })

    await expect(pending).resolves.toBe(false)
    expect(store.media?.id).toBe('media-2')
    expect(store.runs).toEqual([])
    expect(store.notice).toBeNull()
    expect(store.actionError).toBeNull()
    expect(store.working).toBe(false)
  })

  it('accepts a create response scoped to the second PT site and media item', async () => {
    const store = useTorrentStore()
    mocks.ptSiteCatalog.mockResolvedValueOnce(makeCatalog('synthetic-two'))
    await store.loadCatalog()
    await store.load('media-1')
    const createdRun = makeRun('search-two', 'synthetic-two')
    const createdCandidate = makeCandidate(
      'candidate-two',
      'search-two',
      'synthetic-two',
    )
    mocks.torrentCreate.mockResolvedValueOnce({
      ...createdRun,
      job_id: 'job-two',
      deduplicated: false,
    })
    mocks.torrentGet.mockResolvedValueOnce(createdRun)
    mocks.torrentCandidates.mockResolvedValueOnce([createdCandidate])

    const created = await store.create('media-1', {
      site_id: 'synthetic-two',
      preferred_resolutions: ['1080p'],
      preferred_sources: ['WEB-DL'],
      preferred_audio: [],
      preferred_subtitles: [],
    })

    expect(created).toBe(true)
    expect(store.runs[0]).toMatchObject({
      id: 'search-two',
      media_id: 'media-1',
      site_id: 'synthetic-two',
    })
    expect(store.selectedRun).toMatchObject({
      id: 'search-two',
      media_id: 'media-1',
      site_id: 'synthetic-two',
    })
    expect(store.candidates[0]).toMatchObject({
      id: 'candidate-two',
      search_run_id: 'search-two',
      candidate: { site_id: 'synthetic-two' },
    })
    expect(store.notice).toBe(
      'Synthetic Two 只读搜索已创建；任务异步执行，可刷新查看最新结果',
    )
    expect(store.actionError).toBeNull()
  })

  it.each([
    ['site', makeRun('search-mismatch', 'avistaz', 'media-1')],
    ['media', makeRun('search-mismatch', 'synthetic-two', 'media-2')],
  ])('rejects a second-site create response bound to another %s', async (_label, response) => {
    const store = useTorrentStore()
    mocks.ptSiteCatalog.mockResolvedValueOnce(makeCatalog('synthetic-two'))
    await store.loadCatalog()
    await store.load('media-1')
    mocks.torrentCreate.mockResolvedValueOnce({
      ...response,
      job_id: 'job-mismatch',
      deduplicated: false,
    })

    const created = await store.create('media-1', {
      site_id: 'synthetic-two',
      preferred_resolutions: ['1080p'],
      preferred_sources: ['WEB-DL'],
      preferred_audio: [],
      preferred_subtitles: [],
    })

    expect(created).toBe(false)
    expect(store.runs).toEqual([])
    expect(store.notice).toBeNull()
    expect(store.actionError).toContain('响应身份不一致')
  })
})

describe('approval partial-success handling', () => {
  it('keeps a successful approval when the follow-up plan read fails', async () => {
    mocks.approvalApprove.mockResolvedValueOnce({ id: 'approval-1', status: 'APPROVED' })
    mocks.approvalPlan.mockRejectedValueOnce(new Error('计划服务暂时不可用'))

    const store = useApprovalStore()
    await store.approve('approval-1')

    expect(store.approval?.status).toBe('APPROVED')
    expect(store.notice).toContain('审批已通过')
    expect(store.error).toBeNull()
    expect(store.plan).toBeNull()
    expect(store.planError).toContain('审批状态已保存')
    expect(store.planError).toContain('计划服务暂时不可用')
  })
})

describe('status presentation', () => {
  it('normalizes every underscore in multi-part state classes', () => {
    const wrapper = mount(StatusPill, { props: { status: 'CANCELLED_BEFORE_ADD' } })
    expect(wrapper.classes()).toContain('state-cancelled-before-add')
  })
})
