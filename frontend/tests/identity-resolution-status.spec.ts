import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { useAuthStore } from '../src/stores/auth'
import { useIdentityStore } from '../src/stores/identity'
import type {
  JobStatus,
  MediaItem,
  MetadataMatch,
  MetadataResolutionJob,
} from '../src/types'
import IdentityView from '../src/views/IdentityView.vue'

const mocks = vi.hoisted(() => ({
  mediaGet: vi.fn(),
  metadataCandidates: vi.fn(),
  mediaResolve: vi.fn(),
  resolveJob: vi.fn(),
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
    resolveJob: mocks.resolveJob,
    confirmIdentity: mocks.confirmIdentity,
  },
  torrentApi: { list: mocks.torrentList },
}))

function media(id = 'media-1', workflowStatus: MediaItem['workflow_status'] = 'DISCOVERED'): MediaItem {
  return {
    id,
    source: 'nextfind',
    source_item_id: `nextfind:${id}`,
    media_type: 'tv',
    tmdb_id: null,
    title: `测试影视 ${id}`,
    original_title: null,
    year: 2026,
    country_codes: null,
    poster_path: null,
    raw_type: 'tv',
    local_episodes: 2,
    total_episodes: null,
    aired_episodes: null,
    missing_episodes: null,
    discovery_status: 'MISSING',
    identity_confidence: 'NEEDS_CONFIRMATION',
    metadata_status: workflowStatus === 'IDENTITY_REVIEW' ? 'NEEDS_CONFIRMATION' : 'UNRESOLVED',
    workflow_status: workflowStatus,
    discovered_at: '2026-08-11T00:00:00Z',
    updated_at: '2026-08-11T00:00:00Z',
  }
}

function match(mediaId = 'media-1'): MetadataMatch {
  return {
    id: 'match-1',
    media_id: mediaId,
    tmdb_id: 42,
    rank: 1,
    score: 0.98,
    match_reasons: ['TITLE_EXACT'],
    conflicts: [],
    candidate: {
      tmdb_id: 42,
      imdb_id: 'tt0042',
      media_type: 'tv',
      title: '测试影视',
      chinese_title: '测试影视',
      english_title: 'Test Show',
      original_title: 'Test Show',
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
    created_at: '2026-08-11T00:00:00Z',
  }
}

function job(
  status: JobStatus,
  options: {
    mediaId?: string
    jobId?: string
    errorCode?: string | null
    errorMessage?: string | null
  } = {},
): MetadataResolutionJob {
  return {
    media_id: options.mediaId ?? 'media-1',
    job_id: options.jobId ?? 'job-1',
    status,
    error_code: options.errorCode ?? null,
    error_message: options.errorMessage ?? null,
    created_at: '2026-08-11T00:00:00Z',
    updated_at: '2026-08-11T00:00:01Z',
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  mocks.mediaGet.mockResolvedValue(media())
  mocks.metadataCandidates.mockResolvedValue([])
  mocks.torrentList.mockResolvedValue([])
  mocks.mediaResolve.mockResolvedValue({
    media_id: 'media-1',
    job_id: 'job-1',
    status: 'METADATA_PENDING',
    deduplicated: false,
  })
  const auth = useAuthStore()
  auth.principal = { username: 'operator-user', role: 'operator' }
  auth.initialized = true
})

afterEach(() => {
  vi.useRealTimers()
})

describe('identity metadata resolution job polling', () => {
  it('stores the accepted job id, follows every active status, then refreshes media and candidates', async () => {
    vi.useFakeTimers()
    mocks.resolveJob
      .mockResolvedValueOnce(job('PENDING'))
      .mockResolvedValueOnce(job('RUNNING'))
      .mockResolvedValueOnce(job('RETRY_WAIT'))
      .mockResolvedValueOnce(job('SUCCEEDED'))
    mocks.mediaGet
      .mockResolvedValueOnce(media())
      .mockResolvedValueOnce(media('media-1', 'IDENTITY_REVIEW'))
    mocks.metadataCandidates
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([match()])
    const store = useIdentityStore()
    await store.load('media-1')

    const resolution = store.resolve('media-1')
    await vi.runAllTimersAsync()

    await expect(resolution).resolves.toBe(true)
    expect(store.resolutionJobId).toBe('job-1')
    expect(mocks.resolveJob).toHaveBeenCalledTimes(4)
    for (const call of mocks.resolveJob.mock.calls) {
      expect(call[0]).toBe('media-1')
      expect(call[1]).toBe('job-1')
      expect(call[2]).toBeInstanceOf(AbortSignal)
    }
    expect(mocks.mediaGet).toHaveBeenCalledTimes(2)
    expect(mocks.metadataCandidates).toHaveBeenCalledTimes(2)
    expect(store.candidates).toHaveLength(1)
    expect(store.notice).toBe('TMDB 解析已完成，找到 1 个候选，请人工确认。')
    expect(store.error).toBeNull()
  })

  it.each([
    ['FAILED', 'TMDB_RATE_LIMITED', 'TMDB 请求频率超限'],
    ['CANCELLED', 'METADATA_RESOLUTION_SUPERSEDED', '影视身份已在其他流程中确认'],
  ] as const)('shows the real %s error code and message', async (status, errorCode, errorMessage) => {
    mocks.resolveJob.mockResolvedValueOnce(job(status, { errorCode, errorMessage }))
    const store = useIdentityStore()
    await store.load('media-1')

    await expect(store.resolve('media-1')).resolves.toBe(false)

    expect(store.resolutionJobId).toBe('job-1')
    expect(store.error).toBe(`${errorCode} · ${errorMessage}`)
    expect(store.notice).toBeNull()
    expect(mocks.mediaGet).toHaveBeenCalledTimes(1)
    expect(mocks.metadataCandidates).toHaveBeenCalledTimes(1)
  })

  it('stops after the bounded attempts and reports the last real job status without claiming failure', async () => {
    vi.useFakeTimers()
    mocks.resolveJob.mockResolvedValue(job('RETRY_WAIT'))
    const store = useIdentityStore()
    await store.load('media-1')

    const resolution = store.resolve('media-1')
    await vi.runAllTimersAsync()

    await expect(resolution).resolves.toBe(false)
    expect(mocks.resolveJob).toHaveBeenCalledTimes(80)
    expect(store.notice).toContain('等待 TMDB 解析任务已超时')
    expect(store.notice).toContain('最后任务状态：RETRY_WAIT')
    expect(store.notice).toContain('任务仍可能在后台继续')
    expect(store.error).toBeNull()
    expect(store.working).toBe(false)
  })

  it('cancels the in-flight poll when the reused identity route switches media', async () => {
    vi.useFakeTimers()
    mocks.mediaGet.mockImplementation((mediaId: string) => Promise.resolve(media(mediaId)))
    mocks.mediaResolve.mockResolvedValueOnce({
      media_id: 'media-1',
      job_id: 'job-1',
      status: 'METADATA_PENDING',
      deduplicated: false,
    })
    mocks.resolveJob.mockResolvedValue(job('PENDING'))
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/media/:id/identity', component: IdentityView }],
    })
    await router.push('/media/media-1/identity')
    await router.isReady()
    const wrapper = mount(IdentityView, { global: { plugins: [router] } })
    await flushPromises()
    const store = useIdentityStore()

    const resolution = store.resolve('media-1')
    await vi.advanceTimersByTimeAsync(0)
    expect(mocks.resolveJob).toHaveBeenCalledTimes(1)
    expect(store.resolutionJobId).toBe('job-1')

    await router.push('/media/media-2/identity')
    await flushPromises()
    await expect(resolution).resolves.toBe(false)

    expect(mocks.resolveJob).toHaveBeenCalledTimes(1)
    expect(store.media?.id).toBe('media-2')
    expect(store.resolutionJobId).toBeNull()
    expect(store.working).toBe(false)
    expect(store.error).toBeNull()
    wrapper.unmount()
  })

  it('renders terminal job errors and aborts polling when the view unmounts', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/media/:id/identity', component: IdentityView }],
    })
    await router.push('/media/media-1/identity')
    await router.isReady()
    mocks.resolveJob.mockResolvedValueOnce(
      job('FAILED', {
        errorCode: 'TMDB_RESPONSE_INVALID',
        errorMessage: 'TMDB 返回数据无效',
      }),
    )
    const wrapper = mount(IdentityView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.get('.header-actions button.primary').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('TMDB_RESPONSE_INVALID')
    expect(wrapper.text()).toContain('TMDB 返回数据无效')

    vi.useFakeTimers()
    mocks.mediaResolve.mockResolvedValueOnce({
      media_id: 'media-1',
      job_id: 'job-2',
      status: 'METADATA_PENDING',
      deduplicated: false,
    })
    mocks.resolveJob.mockResolvedValue(job('PENDING', { jobId: 'job-2' }))
    const store = useIdentityStore()
    const resolution = store.resolve('media-1')
    await vi.advanceTimersByTimeAsync(0)
    expect(store.resolutionJobId).toBe('job-2')

    wrapper.unmount()
    await expect(resolution).resolves.toBe(false)
    expect(store.resolutionJobId).toBeNull()
    expect(store.working).toBe(false)
  })
})
