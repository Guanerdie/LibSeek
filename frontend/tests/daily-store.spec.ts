import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useDailyStore } from '../src/stores/daily'
import type { DailyDownload, DailyMedia, DailyMediaDetail, DailySearch } from '../src/types'
import DownloadsView from '../src/views/DownloadsView.vue'
import LibraryView from '../src/views/LibraryView.vue'
import ResourcesView from '../src/views/ResourcesView.vue'

const mocks = vi.hoisted(() => ({
  syncMedia: vi.fn(),
  media: vi.fn(),
  mediaDetail: vi.fn(),
  identify: vi.fn(),
  createSearch: vi.fn(),
  setSubscription: vi.fn(),
  setSubscriptions: vi.fn(),
  setMinimumScore: vi.fn(),
  search: vi.fn(),
  downloadCandidate: vi.fn(),
  retryDownload: vi.fn(),
  downloads: vi.fn(),
  syncDownloads: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  dailyApi: mocks,
}))

const media: DailyMedia = {
  id: 'media-1',
  source: 'nextfind',
  source_item_id: 'source-1',
  media_type: 'tv',
  tmdb_id: 100,
  title: '测试剧集',
  original_title: 'Test Show',
  country_codes: ['JP'],
  original_language: 'ja',
  regions: ['日本'],
  year: 2026,
  poster_path: null,
  state: 'READY',
  attention_reason: null,
  discovered_at: '2026-08-22T00:00:00Z',
  updated_at: '2026-08-22T00:00:00Z',
}

const filterOptions = {
  media_types: ['tv'] as const,
  regions: ['欧美', '大陆', '港台', '韩国', '日本', '亚太'] as const,
  states: ['READY'] as const,
  years: [2026],
}

function mediaPage(items: DailyMedia[] = [media]) {
  return {
    items,
    page: 1,
    page_size: 30,
    total: items.length,
    filter_options: filterOptions,
  }
}

const download: DailyDownload = {
  id: 'download-1',
  media_id: media.id,
  candidate_id: 'candidate-1',
  info_hash: 'a'.repeat(40),
  name: 'Test.Show.S01.1080p.WEB-DL',
  state: 'DOWNLOADING',
  progress: 0.5,
  download_speed: 1_000_000,
  upload_speed: 10_000,
  ratio: 0.1,
  error_message: null,
  created_at: '2026-08-22T00:00:00Z',
  updated_at: '2026-08-22T00:01:00Z',
}

const failedSearch: DailySearch = {
  id: 'search-failed',
  media_id: media.id,
  site_ids: ['avistaz'],
  state: 'FAILED',
  error_message: 'PT 站点连接超时',
  created_at: '2026-08-22T00:00:00Z',
  finished_at: '2026-08-22T00:01:00Z',
  candidates: [],
}

const successfulSearch: DailySearch = {
  ...failedSearch,
  id: 'search-succeeded',
  state: 'SUCCEEDED',
  error_message: null,
}

function mediaDetail(overrides: Partial<DailyMediaDetail> = {}): DailyMediaDetail {
  return {
    ...media,
    episodes: [],
    latest_search: null,
    latest_automation: null,
    minimum_score_override: null,
    subscribed: false,
    subscription_active: false,
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve
    reject = promiseReject
  })
  return { promise, reject, resolve }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('simplified daily store', () => {
  it('syncs NextFind and immediately refreshes the missing list', async () => {
    mocks.syncMedia.mockResolvedValueOnce({ created: 1, updated: 0 })
    mocks.media.mockResolvedValueOnce(mediaPage())
    const store = useDailyStore()

    await store.syncMedia()

    expect(mocks.syncMedia).toHaveBeenCalledOnce()
    expect(store.media).toEqual([media])
  })

  it('applies the common library filters using NextFind region groups', async () => {
    mocks.media.mockResolvedValue(mediaPage())
    const wrapper = mount(LibraryView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()

    await wrapper.get('input[type="search"]').setValue('测试')
    const selects = wrapper.findAll('.filter-grid select')
    await selects[0]!.setValue('tv')
    await selects[1]!.setValue('日本')
    await selects[2]!.setValue('READY')
    await selects[3]!.setValue('2026')
    await wrapper.get('form.library-filters').trigger('submit')
    await flushPromises()

    expect(mocks.media).toHaveBeenLastCalledWith({
      query: '测试',
      mediaType: 'tv',
      region: '日本',
      state: 'READY',
      year: 2026,
    })
  })

  it('keeps media syncing while a concurrent download refresh completes', async () => {
    const mediaSync = deferred<{ created: number; updated: number }>()
    mocks.syncMedia.mockReturnValueOnce(mediaSync.promise)
    mocks.media.mockResolvedValueOnce(mediaPage())
    mocks.syncDownloads.mockResolvedValueOnce({ created: 0, updated: 1 })
    mocks.downloads.mockResolvedValueOnce({
      items: [download],
      page: 1,
      page_size: 30,
      total: 1,
    })
    const store = useDailyStore()

    const syncPromise = store.syncMedia()
    expect(store.mediaSyncing).toBe(true)

    await store.refreshDownloads()

    expect(store.downloadsSyncing).toBe(false)
    expect(store.mediaSyncing).toBe(true)

    mediaSync.resolve({ created: 1, updated: 0 })
    await syncPromise
    expect(store.mediaSyncing).toBe(false)
  })

  it('deduplicates a repeated media sync while the first request is running', async () => {
    const mediaSync = deferred<{ created: number; updated: number }>()
    mocks.syncMedia.mockReturnValueOnce(mediaSync.promise)
    mocks.media.mockResolvedValueOnce(mediaPage())
    const store = useDailyStore()

    const firstSync = store.syncMedia()
    await store.syncMedia()

    expect(mocks.syncMedia).toHaveBeenCalledOnce()
    expect(store.mediaSyncing).toBe(true)

    mediaSync.resolve({ created: 1, updated: 0 })
    await firstSync
  })

  it('reloads the missing list after sync and only then clears the syncing state', async () => {
    const mediaSync = deferred<{ created: number; updated: number }>()
    const mediaReload = deferred<ReturnType<typeof mediaPage>>()
    mocks.syncMedia.mockReturnValueOnce(mediaSync.promise)
    mocks.media.mockReturnValueOnce(mediaReload.promise)
    const store = useDailyStore()
    store.media = [media]

    const syncPromise = store.syncMedia()
    expect(store.mediaSyncing).toBe(true)
    expect(store.media).toEqual([media])
    expect(mocks.media).not.toHaveBeenCalled()

    mediaSync.resolve({ created: 1, updated: 0 })
    await Promise.resolve()

    expect(mocks.media).toHaveBeenCalledOnce()
    expect(store.mediaSyncing).toBe(true)
    expect(store.media).toEqual([media])

    mediaReload.resolve(mediaPage([]))
    await syncPromise

    expect(store.media).toEqual([])
    expect(store.mediaSyncing).toBe(false)
  })

  it('keeps list loading until overlapping loads finish and ignores the stale response', async () => {
    const olderLoad = deferred<ReturnType<typeof mediaPage>>()
    const newerLoad = deferred<ReturnType<typeof mediaPage>>()
    const newerMedia = { ...media, id: 'media-2', title: '新列表' }
    mocks.media.mockReturnValueOnce(olderLoad.promise).mockReturnValueOnce(newerLoad.promise)
    const store = useDailyStore()

    const olderPromise = store.loadMedia({ query: '旧条件' })
    const newerPromise = store.loadMedia({ query: '新条件' })

    olderLoad.resolve(mediaPage([media]))
    await olderPromise
    expect(store.mediaListLoading).toBe(true)
    expect(store.media).toEqual([])

    newerLoad.resolve(mediaPage([newerMedia]))
    await newerPromise
    expect(store.mediaListLoading).toBe(false)
    expect(store.media).toEqual([newerMedia])
  })

  it('does not let an older page load overwrite the list refreshed after sync', async () => {
    const staleLoad = deferred<ReturnType<typeof mediaPage>>()
    const refreshedMedia = { ...media, id: 'media-2', title: '同步后列表' }
    mocks.media
      .mockReturnValueOnce(staleLoad.promise)
      .mockResolvedValueOnce(mediaPage([refreshedMedia]))
    mocks.syncMedia.mockResolvedValueOnce({ created: 1, updated: 0 })
    const store = useDailyStore()

    const loadPromise = store.loadMedia()
    await store.syncMedia()
    expect(store.media).toEqual([refreshedMedia])

    staleLoad.resolve(mediaPage([media]))
    await loadPromise
    expect(store.media).toEqual([refreshedMedia])
    expect(store.mediaListLoading).toBe(false)
  })

  it('keeps media sync state and errors isolated from a failed download sync', async () => {
    const mediaSync = deferred<{ created: number; updated: number }>()
    mocks.syncMedia.mockReturnValueOnce(mediaSync.promise)
    mocks.syncDownloads.mockRejectedValueOnce(new Error('qBittorrent unavailable'))
    const store = useDailyStore()

    const syncPromise = store.syncMedia()
    await store.refreshDownloads()

    expect(store.mediaSyncing).toBe(true)
    expect(store.mediaError).toBeNull()
    expect(store.downloadsError).toBe('无法同步下载状态')

    mediaSync.reject(new Error('NextFind unavailable'))
    await syncPromise

    expect(store.mediaSyncing).toBe(false)
    expect(store.mediaError).toBe('无法同步缺失影视')
    expect(store.downloadsError).toBe('无法同步下载状态')
  })

  it('still shows the syncing label after leaving and re-entering the library view', async () => {
    const mediaSync = deferred<{ created: number; updated: number }>()
    const emptyMedia = mediaPage([])
    mocks.syncMedia.mockReturnValueOnce(mediaSync.promise)
    mocks.media
      .mockResolvedValueOnce(emptyMedia)
      .mockResolvedValueOnce(emptyMedia)
      .mockResolvedValueOnce(emptyMedia)
    const store = useDailyStore()
    const firstView = mount(LibraryView)
    await flushPromises()

    await firstView.get('button.primary').trigger('click')
    expect(store.mediaSyncing).toBe(true)
    expect(firstView.get('button.primary').text()).toBe('同步中…')
    expect(firstView.get('button.primary').attributes('disabled')).toBeDefined()

    firstView.unmount()
    const reenteredView = mount(LibraryView)
    await flushPromises()

    expect(reenteredView.get('button.primary').text()).toBe('同步中…')
    expect(reenteredView.get('button.primary').attributes('disabled')).toBeDefined()

    mediaSync.resolve({ created: 1, updated: 0 })
    await flushPromises()
    expect(reenteredView.get('button.primary').text()).toBe('同步缺失影视')
  })

  it('restores and displays a persisted failed search when re-entering resources', async () => {
    mocks.mediaDetail.mockResolvedValueOnce(mediaDetail({ latest_search: failedSearch }))
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/library/:id/resources', component: ResourcesView },
        { path: '/downloads', component: { template: '<div />' } },
      ],
    })
    await router.push(`/library/${media.id}/resources`)
    const wrapper = mount(ResourcesView, { global: { plugins: [router] } })
    await flushPromises()

    expect(mocks.mediaDetail).toHaveBeenCalledWith(media.id)
    expect(mocks.search).not.toHaveBeenCalled()
    expect(wrapper.get('.section-heading').text()).toContain('失败')
    expect(wrapper.get('.error-state').text()).toBe('PT 站点连接超时')
  })

  it('loads the full latest successful search before showing its candidate result', async () => {
    mocks.mediaDetail.mockResolvedValueOnce(mediaDetail({ latest_search: successfulSearch }))
    mocks.search.mockResolvedValueOnce(successfulSearch)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/library/:id/resources', component: ResourcesView },
        { path: '/downloads', component: { template: '<div />' } },
      ],
    })
    await router.push(`/library/${media.id}/resources`)
    const wrapper = mount(ResourcesView, { global: { plugins: [router] } })
    await flushPromises()

    expect(mocks.search).toHaveBeenCalledWith(successfulSearch.id)
    expect(wrapper.get('.empty-state').text()).toBe('没有找到符合条件的资源')
  })

  it('links a candidate title to its public PT details page', async () => {
    const searchWithCandidate: DailySearch = {
      ...successfulSearch,
      candidates: [
        {
          id: 'candidate-linked',
          search_id: successfulSearch.id,
          site_id: 'avistaz',
          torrent_id: '114307',
          title: 'Linked.Show.1080p.WEB-DL',
          details_url: 'https://avistaz.to/torrents/114307',
          size_bytes: 10_000,
          seeders: 6,
          resolution: '1080p',
          source: 'WEB-DL',
          codec: 'H.264',
          download_factor: 0,
          season_coverage: [1],
          episode_coverage: [],
          score: 0.9,
          reasons: [],
          warnings: [],
        },
      ],
    }
    mocks.mediaDetail.mockResolvedValueOnce(mediaDetail({ latest_search: successfulSearch }))
    mocks.search.mockResolvedValueOnce(searchWithCandidate)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/library/:id/resources', component: ResourcesView },
        { path: '/downloads', component: { template: '<div />' } },
      ],
    })
    await router.push(`/library/${media.id}/resources`)
    const wrapper = mount(ResourcesView, { global: { plugins: [router] } })
    await flushPromises()

    const link = wrapper.get('a.candidate-title-link')
    expect(link.text()).toBe('Linked.Show.1080p.WEB-DL')
    expect(link.attributes()).toMatchObject({
      href: 'https://avistaz.to/torrents/114307',
      target: '_blank',
      rel: 'noopener noreferrer',
    })
    expect(wrapper.get('.candidate-free-status').text()).toBe('免费')
    expect(wrapper.get('.candidate-free-status').classes()).toContain('is-free')
  })

  it('clears the previous media search while loading a different media item', async () => {
    const nextDetail = deferred<DailyMediaDetail>()
    mocks.mediaDetail.mockReturnValueOnce(nextDetail.promise)
    const store = useDailyStore()
    store.search = failedSearch

    const loadPromise = store.loadMediaDetail('media-2')

    expect(store.search).toBeNull()
    nextDetail.resolve(
      mediaDetail({
        id: 'media-2',
        source_item_id: 'source-2',
        title: '另一部影视',
        latest_search: null,
      }),
    )
    await loadPromise

    expect(store.selectedMedia?.id).toBe('media-2')
    expect(store.search).toBeNull()
  })

  it('passes the single warning confirmation directly to download submission', async () => {
    mocks.downloadCandidate.mockResolvedValueOnce(download)
    const store = useDailyStore()

    expect(await store.download('candidate-1', true)).toBe(true)
    expect(mocks.downloadCandidate).toHaveBeenCalledWith('candidate-1', true)
  })

  it('shows candidate submission progress and a nearby error when download creation fails', async () => {
    const searchWithCandidate: DailySearch = {
      ...successfulSearch,
      candidates: [
        {
          id: 'candidate-1',
          search_id: successfulSearch.id,
          site_id: 'avistaz',
          torrent_id: '114307',
          title: 'Test.Show.S01.1080p.WEB-DL',
          details_url: 'https://avistaz.to/torrents/114307',
          size_bytes: 10_000,
          seeders: 6,
          resolution: '1080p',
          source: 'WEB-DL',
          codec: 'H.264',
          download_factor: null,
          season_coverage: [1],
          episode_coverage: [],
          score: 0.9,
          reasons: [],
          warnings: [],
        },
      ],
    }
    const submission = deferred<DailyDownload>()
    mocks.mediaDetail.mockResolvedValueOnce(mediaDetail({ latest_search: successfulSearch }))
    mocks.search.mockResolvedValueOnce(searchWithCandidate)
    mocks.downloadCandidate.mockReturnValueOnce(submission.promise)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/library/:id/resources', component: ResourcesView },
        { path: '/downloads', component: { template: '<div />' } },
      ],
    })
    await router.push(`/library/${media.id}/resources`)
    const wrapper = mount(ResourcesView, { global: { plugins: [router] } })
    await flushPromises()

    const button = wrapper.get('.candidate-card .button.primary')
    await button.trigger('click')
    expect(button.text()).toBe('提交中…')
    expect(button.attributes('disabled')).toBeDefined()

    submission.reject(new Error('qBittorrent unavailable'))
    await flushPromises()

    expect(wrapper.get('.candidate-card .inline-error').text()).toBe('无法创建下载')
    expect(button.text()).toBe('重试下载')
  })

  it('retries an errored download through its download record', async () => {
    const failedDownload: DailyDownload = {
      ...download,
      state: 'ERROR',
      error_message: '站点下载链接已失效',
    }
    const retry = deferred<DailyDownload>()
    mocks.syncDownloads.mockResolvedValueOnce({ created: 0, updated: 0 })
    mocks.downloads.mockResolvedValueOnce({
      items: [failedDownload],
      page: 1,
      page_size: 30,
      total: 1,
    })
    mocks.retryDownload.mockReturnValueOnce(retry.promise)
    const wrapper = mount(DownloadsView)
    await flushPromises()

    const button = wrapper.get('.download-card .button.primary')
    await button.trigger('click')
    expect(button.text()).toBe('重试中…')
    expect(button.attributes('disabled')).toBeDefined()

    retry.resolve({ ...failedDownload, state: 'QUEUED', error_message: null })
    await flushPromises()

    expect(mocks.retryDownload).toHaveBeenCalledWith(failedDownload.id)
    expect(useDailyStore().downloads[0]?.state).toBe('QUEUED')
    expect(wrapper.find('.download-card .button.primary').exists()).toBe(false)
    wrapper.unmount()
  })

  it('searches through the result cache by default', async () => {
    mocks.mediaDetail.mockResolvedValueOnce(mediaDetail())
    mocks.createSearch.mockResolvedValueOnce(successfulSearch)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/library/:id/resources', component: ResourcesView }],
    })
    await router.push(`/library/${media.id}/resources`)
    const wrapper = mount(ResourcesView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.get('.button.primary').trigger('click')
    await flushPromises()

    expect(mocks.createSearch).toHaveBeenCalledWith(media.id, ['avistaz'], false)
  })

  it('lets the refresh button skip a cached result', async () => {
    mocks.mediaDetail.mockResolvedValueOnce(mediaDetail())
    mocks.createSearch.mockResolvedValueOnce(successfulSearch)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/library/:id/resources', component: ResourcesView }],
    })
    await router.push(`/library/${media.id}/resources`)
    const wrapper = mount(ResourcesView, { global: { plugins: [router] } })
    await flushPromises()

    const refresh = wrapper
      .findAll('button')
      .find((button) => button.text() === '强制刷新')
    expect(refresh).toBeDefined()
    await refresh!.trigger('click')
    await flushPromises()

    expect(mocks.createSearch).toHaveBeenCalledWith(media.id, ['avistaz'], true)
  })

  it('explains why the last automation run picked nothing', async () => {
    mocks.mediaDetail.mockResolvedValueOnce(
      mediaDetail({
        latest_automation: {
          job_id: 'j1',
          state: 'SUCCEEDED',
          created_at: '2026-09-08T08:00:00Z',
          finished_at: null,
          error_code: null,
          error_message: null,
          candidate_count: 40,
          selected_title: null,
          selected_score: null,
          search_cooldown_until: null,
          download_skipped: null,
          rejected: [
            { candidate_id: null, title: 'A', reasons: ['评分低于策略门槛', '做种数不足'] },
            { candidate_id: null, title: 'B', reasons: ['评分低于策略门槛'] },
          ],
        },
      }),
    )
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/library/:id/resources', component: ResourcesView }],
    })
    await router.push(`/library/${media.id}/resources`)
    const wrapper = mount(ResourcesView, { global: { plugins: [router] } })
    await flushPromises()

    const panel = wrapper.get('.outcome-panel')
    expect(panel.text()).toContain('40')
    // Reasons are counted and ordered by how often they bit.
    const reasons = wrapper.findAll('.outcome-reasons li').map((li) => li.text())
    expect(reasons[0]).toContain('评分低于策略门槛')
    expect(reasons[0]).toContain('2')
    expect(reasons[1]).toContain('做种数不足')
  })

  it('warns when a subscription cannot take effect', async () => {
    mocks.mediaDetail.mockResolvedValueOnce(
      mediaDetail({ subscribed: true, subscription_active: false }),
    )
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/library/:id/resources', component: ResourcesView }],
    })
    await router.push(`/library/${media.id}/resources`)
    const wrapper = mount(ResourcesView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.get('.outcome-warning').text()).toContain('不会生效')
  })

  it('subscribes to a show from its own page', async () => {
    mocks.mediaDetail.mockResolvedValueOnce(mediaDetail({ subscribed: false }))
    mocks.setSubscription.mockResolvedValueOnce(
      mediaDetail({ subscribed: true, subscription_active: true }),
    )
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/library/:id/resources', component: ResourcesView }],
    })
    await router.push(`/library/${media.id}/resources`)
    const wrapper = mount(ResourcesView, { global: { plugins: [router] } })
    await flushPromises()

    const button = wrapper.findAll('button').find((item) => item.text() === '追这部')
    expect(button).toBeDefined()
    await button!.trigger('click')
    await flushPromises()

    expect(mocks.setSubscription).toHaveBeenCalledWith(media.id, true)
    expect(
      wrapper.findAll('button').some((item) => item.text() === '取消追更'),
    ).toBe(true)
  })

  it('shows library states in Chinese, not the raw enum', async () => {
    mocks.media.mockResolvedValueOnce(
      mediaPage([
        { ...media, id: 'a', state: 'READY' },
        { ...media, id: 'b', state: 'CANDIDATES' },
        { ...media, id: 'c', state: 'NEEDS_ATTENTION' },
      ]),
    )
    const wrapper = mount(LibraryView, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    const pills = wrapper.findAll('.status-pill').map((pill) => pill.text())
    expect(pills).toEqual(['待搜索', '有候选待确认', '需要处理'])
  })

  it('adds the whole filtered page to the follow list', async () => {
    mocks.media.mockResolvedValueOnce(mediaPage([media]))
    mocks.setSubscriptions.mockResolvedValueOnce({ subscribed_total: 1, changed: 1 })
    const wrapper = mount(LibraryView, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    const button = wrapper
      .findAll('button')
      .find((item) => item.text().startsWith('本页加入追更'))
    expect(button).toBeDefined()
    await button!.trigger('click')
    await flushPromises()

    expect(mocks.setSubscriptions).toHaveBeenCalledWith([media.id], true)
    expect(wrapper.get('.bulk-actions').text()).toContain('新增 1 项')
  })

  it('sets a per-media score override, and treats an empty box as clearing it', async () => {
    mocks.mediaDetail.mockResolvedValueOnce(mediaDetail({ minimum_score_override: 0.45 }))
    mocks.setMinimumScore.mockResolvedValue(mediaDetail({ minimum_score_override: null }))
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/library/:id/resources', component: ResourcesView }],
    })
    await router.push(`/library/${media.id}/resources`)
    const wrapper = mount(ResourcesView, { global: { plugins: [router] } })
    await flushPromises()

    const input = wrapper.get('input[name="minimum_score_override"]')
    expect((input.element as HTMLInputElement).value).toBe('0.45')

    await input.setValue('')
    await wrapper.findAll('button').find((item) => item.text() === '保存')!.trigger('click')
    await flushPromises()

    expect(mocks.setMinimumScore).toHaveBeenCalledWith(media.id, null)
  })

  it('keeps a zero override instead of treating it as unset', async () => {
    mocks.mediaDetail.mockResolvedValueOnce(mediaDetail())
    mocks.setMinimumScore.mockResolvedValue(mediaDetail({ minimum_score_override: 0 }))
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/library/:id/resources', component: ResourcesView }],
    })
    await router.push(`/library/${media.id}/resources`)
    const wrapper = mount(ResourcesView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.get('input[name="minimum_score_override"]').setValue('0')
    await wrapper.findAll('button').find((item) => item.text() === '保存')!.trigger('click')
    await flushPromises()

    expect(mocks.setMinimumScore).toHaveBeenCalledWith(media.id, 0)
  })

  it('refreshes qBittorrent state before displaying downloads', async () => {
    mocks.syncDownloads.mockResolvedValueOnce({ created: 0, updated: 1 })
    mocks.downloads.mockResolvedValueOnce({
      items: [download],
      page: 1,
      page_size: 30,
      total: 1,
    })
    const store = useDailyStore()

    await store.refreshDownloads()

    expect(mocks.syncDownloads).toHaveBeenCalledOnce()
    expect(store.downloads).toEqual([download])
  })
})
