import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '../src/stores/auth'
import { useMediaStore } from '../src/stores/media'
import type { DiscoveryRun, MediaItem } from '../src/types'
import MediaView from '../src/views/MediaView.vue'

const mocks = vi.hoisted(() => ({
  mediaList: vi.fn(),
  discoveryCreate: vi.fn(),
  discoveryGet: vi.fn(),
}))

vi.mock('../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return {
    ...actual,
    mediaApi: { ...actual.mediaApi, list: mocks.mediaList },
    discoveryApi: {
      ...actual.discoveryApi,
      create: mocks.discoveryCreate,
      get: mocks.discoveryGet,
    },
  }
})

const completedRun: DiscoveryRun = {
  id: 'run-media-follow-up',
  source: 'nextfind',
  status: 'SUCCEEDED',
  started_at: '2026-08-15T00:00:00Z',
  finished_at: '2026-08-15T00:00:01Z',
  discovered_count: 1,
  created_count: 1,
  updated_count: 0,
  error_code: null,
  error_message: null,
  created_at: '2026-08-15T00:00:00Z',
}

function media(workflowStatus: MediaItem['workflow_status']): MediaItem {
  return {
    id: 'media-follow-up',
    source: 'nextfind',
    source_item_id: 'source-follow-up',
    media_type: 'tv',
    tmdb_id: 42,
    title: '测试剧集',
    original_title: 'Test Series',
    year: 2026,
    country_codes: ['JP'],
    poster_path: null,
    raw_type: 'tv',
    local_episodes: 2,
    total_episodes: 8,
    aired_episodes: 6,
    missing_episodes: ['S01E03'],
    discovery_status: 'MISSING',
    identity_confidence: 'HIGH',
    metadata_status: workflowStatus === 'TORRENT_REVIEW' ? 'RESOLVED' : 'UNRESOLVED',
    workflow_status: workflowStatus,
    discovered_at: '2026-08-15T00:00:00Z',
    updated_at: '2026-08-15T00:00:00Z',
  }
}

function page(item: MediaItem) {
  return { items: [item], page: 1, page_size: 20, total: 1 }
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve: (value: T) => void = () => undefined
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  vi.clearAllMocks()
  mocks.discoveryCreate.mockResolvedValue(completedRun)
  const auth = useAuthStore()
  auth.principal = { username: 'operator-user', role: 'operator' }
  auth.initialized = true
})

afterEach(() => {
  useMediaStore().cancelSync()
  vi.useRealTimers()
})

describe('media sync follow-up polling', () => {
  it('keeps refreshing existing rows until asynchronous identity and PT work settles', async () => {
    const statuses: MediaItem['workflow_status'][] = [
      'DISCOVERED',
      'METADATA_PENDING',
      'IDENTITY_CONFIRMED',
      'PT_SEARCH_PENDING',
      'PT_SEARCHING',
      'TORRENT_REVIEW',
    ]
    for (const status of statuses) mocks.mediaList.mockResolvedValueOnce(page(media(status)))

    const store = useMediaStore()
    const syncing = store.syncNextFind()
    await flushPromises()

    expect(store.syncing).toBe(true)
    expect(store.items[0]?.workflow_status).toBe('DISCOVERED')
    expect(mocks.mediaList).toHaveBeenCalledTimes(1)

    for (const status of statuses.slice(1)) {
      await vi.advanceTimersByTimeAsync(1_500)
      expect(store.items[0]?.workflow_status).toBe(status)
    }
    await expect(syncing).resolves.toBe(true)

    expect(store.syncing).toBe(false)
    expect(store.syncNotice).toContain('同步完成')
    expect(store.syncNotice).not.toContain('后台运行')
    await vi.advanceTimersByTimeAsync(6_000)
    expect(mocks.mediaList).toHaveBeenCalledTimes(statuses.length)
    expect(mocks.discoveryGet).not.toHaveBeenCalled()
  })

  it('aborts an in-flight list refresh on view unmount and ignores its response', async () => {
    const delayedRefresh = deferred<ReturnType<typeof page>>()
    mocks.mediaList
      .mockResolvedValueOnce(page(media('IDENTITY_REVIEW')))
      .mockResolvedValueOnce(page(media('METADATA_PENDING')))
      .mockReturnValueOnce(delayedRefresh.promise)

    const wrapper = mount(MediaView)
    await flushPromises()
    const store = useMediaStore()
    const syncing = store.syncNextFind()
    await flushPromises()
    expect(store.items[0]?.workflow_status).toBe('METADATA_PENDING')

    await vi.advanceTimersByTimeAsync(1_500)
    expect(mocks.mediaList).toHaveBeenCalledTimes(3)
    const signal = mocks.mediaList.mock.calls[2]?.[1] as AbortSignal
    expect(signal.aborted).toBe(false)

    wrapper.unmount()
    expect(signal.aborted).toBe(true)
    delayedRefresh.resolve(page(media('TORRENT_REVIEW')))
    await expect(syncing).resolves.toBe(false)

    expect(store.syncing).toBe(false)
    expect(store.items[0]?.workflow_status).toBe('METADATA_PENDING')
    await vi.advanceTimersByTimeAsync(6_000)
    expect(mocks.mediaList).toHaveBeenCalledTimes(3)
  })

  it('stops after the bounded follow-up window when work remains active', async () => {
    mocks.mediaList.mockResolvedValue(page(media('METADATA_PENDING')))
    const store = useMediaStore()

    const syncing = store.syncNextFind()
    await flushPromises()
    await vi.runAllTimersAsync()
    await expect(syncing).resolves.toBe(true)

    expect(store.syncing).toBe(false)
    expect(mocks.mediaList).toHaveBeenCalledTimes(41)
    expect(store.syncNotice).toContain('后续处理仍在后台运行')
  })
})
