import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import { useAuthStore } from '../src/stores/auth'
import { useIdentityStore } from '../src/stores/identity'
import { useTorrentStore } from '../src/stores/torrents'
import type { MediaItem } from '../src/types'
import IdentityView from '../src/views/IdentityView.vue'
import MediaView from '../src/views/MediaView.vue'
import TorrentCandidatesView from '../src/views/TorrentCandidatesView.vue'

const mocks = vi.hoisted(() => ({
  mediaList: vi.fn(),
  mediaGet: vi.fn(),
  mediaResolve: vi.fn(),
  metadataCandidates: vi.fn(),
  confirmIdentity: vi.fn(),
  ptSiteCatalog: vi.fn(),
  torrentList: vi.fn(),
  torrentGet: vi.fn(),
  torrentCandidates: vi.fn(),
  torrentCreate: vi.fn(),
  approvalConfirmDownload: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'media-1' } }),
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  setApiCsrfToken: vi.fn(),
  authApi: {},
  mediaApi: {
    list: mocks.mediaList,
    get: mocks.mediaGet,
    resolve: mocks.mediaResolve,
    metadataCandidates: mocks.metadataCandidates,
    confirmIdentity: mocks.confirmIdentity,
  },
  ptSiteApi: { catalog: mocks.ptSiteCatalog },
  torrentApi: {
    list: mocks.torrentList,
    get: mocks.torrentGet,
    candidates: mocks.torrentCandidates,
    create: mocks.torrentCreate,
  },
  approvalApi: { confirmDownload: mocks.approvalConfirmDownload },
}))

function mediaWithStatus(
  workflowStatus: MediaItem['workflow_status'],
  id = 'media-1',
): MediaItem {
  return {
    id,
    source: 'nextfind',
    source_item_id: `nextfind:${id}`,
    media_type: 'tv',
    tmdb_id: 42,
    title: `测试剧集 ${workflowStatus}`,
    original_title: 'Test Series',
    year: 2026,
    country_codes: null,
    poster_path: null,
    raw_type: 'tv',
    local_episodes: 2,
    total_episodes: 8,
    aired_episodes: 6,
    missing_episodes: ['S01E03'],
    discovery_status: 'MISSING',
    identity_confidence: workflowStatus === 'DISCOVERED' ? 'NEEDS_CONFIRMATION' : 'HIGH',
    metadata_status: workflowStatus === 'DISCOVERED' ? 'UNRESOLVED' : 'RESOLVED',
    workflow_status: workflowStatus,
    discovered_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
  }
}

const availableSite = {
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
}

const searchRun = {
  id: 'search-1',
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

const metadataMatch = {
  id: 'match-1',
  media_id: 'media-1',
  tmdb_id: 42,
  rank: 1,
  score: 0.96,
  match_reasons: ['TMDB_ID_EXACT'],
  conflicts: [],
  created_at: '2026-08-10T00:00:00Z',
  candidate: {
    tmdb_id: 42,
    imdb_id: 'tt0042',
    media_type: 'tv',
    title: '测试剧集',
    chinese_title: '测试剧集',
    english_title: 'Test Series',
    original_title: 'Test Series',
    original_language: 'en',
    country_codes: null,
    aliases: [],
    year: 2026,
    number_of_seasons: 1,
    number_of_episodes: 8,
    episode_matrix: { 1: [1, 2, 3] },
    poster_path: null,
    backdrop_path: null,
    status: 'Returning Series',
    confidence: 1,
    external_ids: { imdb_id: 'tt0042' },
  },
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  mocks.ptSiteCatalog.mockResolvedValue({
    default_site_id: 'avistaz',
    sites: [availableSite],
  })
  mocks.torrentList.mockResolvedValue([])
  const auth = useAuthStore()
  auth.principal = { username: 'operator-user', role: 'operator' }
  auth.initialized = true
})

describe('身份确认到 PT 搜索的门禁', () => {
  it('在媒体列表阻断三种未确认状态，并保留所有确认后状态的 PT 入口', async () => {
    const unconfirmedStatuses: MediaItem['workflow_status'][] = [
      'DISCOVERED',
      'METADATA_PENDING',
      'IDENTITY_REVIEW',
    ]
    const confirmedStatuses: MediaItem['workflow_status'][] = [
      'IDENTITY_CONFIRMED',
      'PT_SEARCH_PENDING',
      'PT_SEARCHING',
      'TORRENT_REVIEW',
      'NO_CANDIDATE',
      'SEARCH_FAILED',
    ]
    const items = [...unconfirmedStatuses, ...confirmedStatuses].map((status, index) =>
      mediaWithStatus(status, `media-${index}`),
    )
    mocks.mediaList.mockResolvedValue({ items, page: 1, page_size: 20, total: items.length })

    const wrapper = mount(MediaView)
    await flushPromises()

    for (const [index, status] of [...unconfirmedStatuses, ...confirmedStatuses].entries()) {
      const row = wrapper.findAll('tbody tr')[index]
      const torrentEntry = row.find(`a[href="/media/media-${index}/torrents"]`)
      if (unconfirmedStatuses.includes(status)) {
        expect(torrentEntry.exists()).toBe(false)
        expect(row.get(`a[href="/media/media-${index}/identity"]`).text()).toBe('确认身份')
        expect(row.get('button[disabled]').text()).toContain('需先确认')
      } else {
        expect(torrentEntry.text()).toBe('PT 候选')
        expect(row.get(`a[href="/media/media-${index}/identity"]`).text()).toBe('身份')
      }
    }
  })

  it.each([
    ['operator', '必须关联一条已确认的身份审核记录'],
    ['viewer', '身份确认需要操作者权限'],
  ] as const)('对 %s 阻断无历史搜索的 IDENTITY_REVIEW', async (role, message) => {
    const auth = useAuthStore()
    auth.principal = { username: `${role}-user`, role }
    mocks.mediaGet.mockResolvedValue(mediaWithStatus('IDENTITY_REVIEW'))
    mocks.torrentList.mockResolvedValue([])

    const wrapper = mount(TorrentCandidatesView)
    await flushPromises()

    expect(wrapper.get('.identity-required').text()).toContain(message)
    expect(wrapper.get('.identity-required a').attributes('href')).toBe('/media/media-1/identity')
    expect(wrapper.find('.search-preferences').exists()).toBe(false)
    expect(mocks.torrentCreate).not.toHaveBeenCalled()

    const created = await useTorrentStore().create('media-1', {
      site_id: 'avistaz',
      preferred_resolutions: [],
      preferred_sources: [],
      preferred_audio: [],
      preferred_subtitles: [],
    })
    expect(created).toBe(false)
    expect(useTorrentStore().actionError).toContain('必须先确认影视身份')
    expect(mocks.torrentCreate).not.toHaveBeenCalled()
  })

  it('确认后允许操作者创建搜索，但查看者仍只读', async () => {
    mocks.mediaGet.mockResolvedValue(mediaWithStatus('IDENTITY_CONFIRMED'))
    mocks.torrentList.mockResolvedValue([])
    mocks.torrentCreate.mockResolvedValue({
      ...searchRun,
      status: 'PT_SEARCH_PENDING',
      job_id: 'job-1',
      deduplicated: false,
    })
    mocks.torrentGet.mockResolvedValue(searchRun)
    mocks.torrentCandidates.mockResolvedValue([])

    const wrapper = mount(TorrentCandidatesView)
    await flushPromises()

    const submit = wrapper.get('.search-preferences button[type="submit"]')
    expect(submit.attributes('disabled')).toBeUndefined()

    const auth = useAuthStore()
    auth.principal = { username: 'viewer-user', role: 'viewer' }
    await nextTick()
    expect(submit.attributes('disabled')).toBeDefined()
    expect(wrapper.get('.permission-bar').text()).toContain('当前角色仅可查看')

    auth.principal = { username: 'operator-user', role: 'operator' }
    await nextTick()
    expect(submit.attributes('disabled')).toBeUndefined()
    await wrapper.get('.search-preferences').trigger('submit')
    await flushPromises()
    expect(mocks.torrentCreate).toHaveBeenCalledWith(
      'media-1',
      expect.objectContaining({ site_id: 'avistaz' }),
    )
  })

  it('将已有后端搜索运行视为历史身份确认证据，兼容回退的媒体状态', async () => {
    mocks.mediaGet.mockResolvedValue(mediaWithStatus('IDENTITY_REVIEW'))
    mocks.torrentList.mockResolvedValue([searchRun])
    mocks.torrentGet.mockResolvedValue(searchRun)
    mocks.torrentCandidates.mockResolvedValue([])

    const wrapper = mount(TorrentCandidatesView)
    await flushPromises()

    expect(useTorrentStore().identityGatePassed).toBe(true)
    expect(wrapper.find('.identity-required').exists()).toBe(false)
    expect(wrapper.get('.search-preferences button[type="submit"]').attributes('disabled')).toBeUndefined()
  })

  it('可从媒体列表身份入口恢复状态回退但已有历史搜索的条目', async () => {
    const regressedMedia = mediaWithStatus('IDENTITY_REVIEW')
    mocks.mediaList.mockResolvedValueOnce({
      items: [regressedMedia],
      page: 1,
      page_size: 20,
      total: 1,
    })
    const mediaWrapper = mount(MediaView)
    await flushPromises()
    const identityEntry = mediaWrapper.get('a[href="/media/media-1/identity"]')
    expect(identityEntry.text()).toBe('确认身份')
    expect(mediaWrapper.find('a[href="/media/media-1/torrents"]').exists()).toBe(false)

    mocks.mediaGet.mockResolvedValueOnce(regressedMedia)
    mocks.metadataCandidates.mockResolvedValueOnce([metadataMatch])
    mocks.torrentList.mockResolvedValueOnce([searchRun])
    const identityWrapper = mount(IdentityView)
    await flushPromises()

    expect(useIdentityStore().identityGatePassed).toBe(true)
    expect(identityWrapper.get('[data-testid="continue-pt-search"]').attributes('href')).toBe(
      '/media/media-1/torrents',
    )
    expect(identityWrapper.get('.header-actions button.primary').attributes('disabled')).toBeDefined()
    expect(identityWrapper.get('.confirm-button').attributes('disabled')).toBeDefined()
    expect(mocks.mediaResolve).not.toHaveBeenCalled()
    expect(mocks.confirmIdentity).not.toHaveBeenCalled()
  })

  it('确认身份后响应最新媒体状态并显示 PT 候选入口', async () => {
    const auth = useAuthStore()
    auth.principal = { username: 'viewer-user', role: 'viewer' }
    mocks.mediaGet
      .mockResolvedValueOnce(mediaWithStatus('IDENTITY_REVIEW'))
      .mockResolvedValueOnce(mediaWithStatus('IDENTITY_CONFIRMED'))
    mocks.metadataCandidates.mockResolvedValue([metadataMatch])
    mocks.confirmIdentity.mockResolvedValue({ status: 'CONFIRMED' })

    const wrapper = mount(IdentityView)
    await flushPromises()

    expect(wrapper.find('[data-testid="continue-pt-search"]').exists()).toBe(false)
    expect(wrapper.get('.confirm-button').attributes('disabled')).toBeDefined()

    auth.principal = { username: 'operator-user', role: 'operator' }
    await nextTick()
    expect(wrapper.get('.confirm-button').attributes('disabled')).toBeUndefined()
    await wrapper.get('.confirm-button').trigger('click')
    await flushPromises()

    expect(mocks.confirmIdentity).toHaveBeenCalledWith('media-1', 'match-1')
    expect(useIdentityStore().media?.workflow_status).toBe('IDENTITY_CONFIRMED')
    const nextEntry = wrapper.get('[data-testid="continue-pt-search"]')
    expect(nextEntry.text()).toBe('查看 PT 候选')
    expect(nextEntry.attributes('href')).toBe('/media/media-1/torrents')
  })
})
