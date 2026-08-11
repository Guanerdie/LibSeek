import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { useAuthStore } from '../src/stores/auth'
import { useIdentityStore } from '../src/stores/identity'
import type { MediaItem, MetadataMatch, TorrentSearchRun } from '../src/types'
import IdentityView from '../src/views/IdentityView.vue'

const mocks = vi.hoisted(() => ({
  mediaGet: vi.fn(),
  metadataCandidates: vi.fn(),
  mediaResolve: vi.fn(),
  confirmIdentity: vi.fn(),
  torrentList: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  setApiCsrfToken: vi.fn(),
  authApi: {},
  mediaApi: {
    get: mocks.mediaGet,
    metadataCandidates: mocks.metadataCandidates,
    resolve: mocks.mediaResolve,
    confirmIdentity: mocks.confirmIdentity,
  },
  torrentApi: { list: mocks.torrentList },
}))

function media(id: string, title: string): MediaItem {
  return {
    id,
    source: 'nextfind',
    source_item_id: `nextfind:${id}`,
    media_type: 'tv',
    tmdb_id: 42,
    title,
    original_title: title,
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
    workflow_status: 'IDENTITY_CONFIRMED',
    discovered_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  const auth = useAuthStore()
  auth.principal = { username: 'operator-user', role: 'operator' }
  auth.initialized = true
})

describe('IdentityView route reuse', () => {
  it('loads the new media id and ignores late responses from the previous id', async () => {
    let resolveOldMedia: (value: MediaItem) => void = () => undefined
    let resolveOldCandidates: (value: MetadataMatch[]) => void = () => undefined
    let resolveOldRuns: (value: TorrentSearchRun[]) => void = () => undefined
    mocks.mediaGet
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveOldMedia = resolve
        }),
      )
      .mockResolvedValueOnce(media('media-2', '新媒体'))
    mocks.metadataCandidates
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveOldCandidates = resolve
        }),
      )
      .mockResolvedValueOnce([])
    mocks.torrentList
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveOldRuns = resolve
        }),
      )
      .mockResolvedValueOnce([])

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/media/:id/identity', component: IdentityView }],
    })
    await router.push('/media/media-1/identity')
    await router.isReady()
    const wrapper = mount(IdentityView, { global: { plugins: [router] } })
    await flushPromises()
    expect(mocks.mediaGet).toHaveBeenCalledWith('media-1')

    await router.push('/media/media-2/identity')
    await flushPromises()
    expect(mocks.mediaGet).toHaveBeenCalledWith('media-2')
    expect(useIdentityStore().media?.id).toBe('media-2')
    expect(wrapper.text()).toContain('新媒体')
    expect(wrapper.get('[data-testid="continue-pt-search"]').attributes('href')).toBe(
      '/media/media-2/torrents',
    )

    resolveOldMedia(media('media-1', '旧媒体'))
    resolveOldCandidates([])
    resolveOldRuns([])
    await flushPromises()

    expect(useIdentityStore().media?.id).toBe('media-2')
    expect(wrapper.text()).toContain('新媒体')
    expect(wrapper.text()).not.toContain('旧媒体')
    expect(wrapper.get('[data-testid="continue-pt-search"]').attributes('href')).toBe(
      '/media/media-2/torrents',
    )
  })

  it('does not let a late confirmation reload the previous route id', async () => {
    let resolveConfirmation: (value: unknown) => void = () => undefined
    mocks.mediaGet
      .mockResolvedValueOnce(media('media-1', '旧路由媒体'))
      .mockResolvedValueOnce(media('media-2', '当前路由媒体'))
    mocks.metadataCandidates.mockResolvedValue([])
    mocks.torrentList.mockResolvedValue([])
    mocks.confirmIdentity.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveConfirmation = resolve
      }),
    )

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/media/:id/identity', component: IdentityView }],
    })
    await router.push('/media/media-1/identity')
    await router.isReady()
    const wrapper = mount(IdentityView, { global: { plugins: [router] } })
    await flushPromises()
    const store = useIdentityStore()

    const confirmation = store.confirm('media-1', 'match-1')
    await flushPromises()
    await router.push('/media/media-2/identity')
    await flushPromises()
    expect(store.media?.id).toBe('media-2')

    resolveConfirmation({ status: 'CONFIRMED' })
    await confirmation
    await flushPromises()

    expect(mocks.mediaGet).toHaveBeenCalledTimes(2)
    expect(store.media?.id).toBe('media-2')
    expect(wrapper.text()).toContain('当前路由媒体')
    expect(wrapper.text()).not.toContain('旧路由媒体')
  })
})
