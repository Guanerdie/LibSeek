import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDailyStore } from '../src/stores/daily'
import type { DailySearch, LibrarySyncStatus } from '../src/types'

const mocks = vi.hoisted(() => ({
  syncMedia: vi.fn(),
  syncStatus: vi.fn(),
  media: vi.fn(),
  mediaDetail: vi.fn(),
  createSearch: vi.fn(),
  search: vi.fn(),
  quickFill: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  dailyApi: mocks,
}))

const runningSearch: DailySearch = {
  id: 'search-1',
  media_id: 'media-1',
  site_ids: ['avistaz'],
  state: 'RUNNING',
  error_message: null,
  created_at: '2026-09-10T00:00:00Z',
  finished_at: null,
  candidates: [],
}

const finishedSearch: DailySearch = {
  ...runningSearch,
  state: 'SUCCEEDED',
  finished_at: '2026-09-10T00:00:30Z',
  candidates: [
    {
      id: 'c1',
      search_id: 'search-1',
      site_id: 'avistaz',
      torrent_id: '1',
      title: 'Background.2026.1080p.WEB-DL',
      details_url: null,
      size_bytes: 1_000,
      seeders: 3,
      resolution: '1080p',
      source: 'WEB-DL',
      codec: null,
      download_factor: null,
      season_coverage: [],
      episode_coverage: [],
      score: 0.8,
      reasons: [],
      warnings: [],
    },
  ],
}

function syncStatus(overrides: Partial<LibrarySyncStatus> = {}): LibrarySyncStatus {
  return {
    state: 'RUNNING',
    created: 0,
    updated: 0,
    error_message: null,
    started_at: '2026-09-10T00:00:00Z',
    finished_at: null,
    ...overrides,
  }
}

const emptyPage = {
  items: [],
  total: 0,
  page: 1,
  page_size: 30,
  filter_options: { media_types: [], regions: [], states: [], years: [] },
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('background searches', () => {
  it('follows a search until the site answers', async () => {
    mocks.createSearch.mockResolvedValueOnce(runningSearch)
    mocks.search.mockResolvedValueOnce(runningSearch).mockResolvedValueOnce(finishedSearch)
    const store = useDailyStore()

    const searching = store.startSearch('media-1')
    await vi.advanceTimersByTimeAsync(2000)
    await searching

    expect(mocks.search).toHaveBeenCalledTimes(2)
    expect(store.search?.state).toBe('SUCCEEDED')
    expect(store.search?.candidates).toHaveLength(1)
  })

  it('stops polling once the page lets go of the search', async () => {
    mocks.createSearch.mockResolvedValueOnce(runningSearch)
    const store = useDailyStore()

    const searching = store.startSearch('media-1')
    await vi.advanceTimersByTimeAsync(0)
    store.stopFollowingSearch()
    await vi.advanceTimersByTimeAsync(5000)
    await searching

    expect(mocks.search).not.toHaveBeenCalled()
  })

  it('quick fill waits for its search, then reuses the finished result', async () => {
    mocks.createSearch.mockResolvedValueOnce(runningSearch)
    mocks.search.mockResolvedValueOnce(finishedSearch)
    mocks.quickFill.mockResolvedValueOnce({
      search: finishedSearch,
      selected_candidate_id: 'c1',
      download: null,
      rejected: [],
    })
    mocks.mediaDetail.mockResolvedValueOnce({})
    const store = useDailyStore()

    const filling = store.quickFill('media-1')
    await vi.advanceTimersByTimeAsync(1000)
    const result = await filling

    expect(mocks.createSearch).toHaveBeenCalledWith('media-1', undefined, false)
    expect(mocks.quickFill).toHaveBeenCalledWith('media-1', false)
    expect(mocks.search.mock.invocationCallOrder[0]!).toBeLessThan(
      mocks.quickFill.mock.invocationCallOrder[0]!,
    )
    expect(result?.selected_candidate_id).toBe('c1')
  })

  it('does not quick fill after the search failed', async () => {
    mocks.createSearch.mockResolvedValueOnce({
      ...runningSearch,
      state: 'FAILED',
      error_message: 'PT 站点暂时不可用',
    })
    const store = useDailyStore()

    expect(await store.quickFill('media-1')).toBeNull()
    expect(mocks.quickFill).not.toHaveBeenCalled()
    expect(store.resourceError).toBe('PT 站点暂时不可用')
  })
})

describe('background NextFind sync', () => {
  it('waits for the sync to finish before reloading the list', async () => {
    mocks.syncMedia.mockResolvedValueOnce(syncStatus())
    mocks.syncStatus
      .mockResolvedValueOnce(syncStatus())
      .mockResolvedValueOnce(syncStatus({ state: 'SUCCEEDED', created: 2, updated: 1 }))
    mocks.media.mockResolvedValueOnce(emptyPage)
    const store = useDailyStore()

    const syncing = store.syncMedia()
    await vi.advanceTimersByTimeAsync(1500)
    expect(mocks.media).not.toHaveBeenCalled()
    expect(store.mediaSyncing).toBe(true)
    await vi.advanceTimersByTimeAsync(1500)
    await syncing

    expect(mocks.syncStatus).toHaveBeenCalledTimes(2)
    expect(mocks.media).toHaveBeenCalledOnce()
    expect(store.mediaSyncing).toBe(false)
  })

  it('shows why a background sync failed', async () => {
    mocks.syncMedia.mockResolvedValueOnce(syncStatus())
    mocks.syncStatus.mockResolvedValueOnce(
      syncStatus({ state: 'FAILED', error_message: 'NextFind 登录失败' }),
    )
    const store = useDailyStore()

    const syncing = store.syncMedia()
    await vi.advanceTimersByTimeAsync(1500)
    await syncing

    expect(store.mediaError).toBe('NextFind 登录失败')
    expect(mocks.media).not.toHaveBeenCalled()
    expect(store.mediaSyncing).toBe(false)
  })
})
