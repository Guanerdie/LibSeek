import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useDashboardStore } from '../src/stores/dashboard'
import type { DownloadJob, MediaItem, Page, SystemStatus } from '../src/types'

const mocks = vi.hoisted(() => ({
  systemStatus: vi.fn(),
  mediaList: vi.fn(),
  downloadJobList: vi.fn(),
  discoveryCreate: vi.fn(),
  discoveryGet: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  systemApi: { status: mocks.systemStatus },
  mediaApi: { list: mocks.mediaList },
  downloadJobApi: { list: mocks.downloadJobList },
  discoveryApi: { create: mocks.discoveryCreate, get: mocks.discoveryGet },
}))

const systemStatus: SystemStatus = {
  api: { healthy: true, message: 'API 运行正常', checked_at: '2026-08-11T00:00:00Z' },
  worker: { healthy: true, message: 'Worker 运行正常', checked_at: '2026-08-11T00:00:00Z' },
  postgres: { healthy: true, message: '数据库正常', checked_at: '2026-08-11T00:00:00Z' },
  nextfind_configured: true,
  tmdb_configured: true,
  tmdb_live_enabled: true,
  pt_site_architecture: 'avistaz',
  pt_site_label: 'PT 站点',
  pt_site_configured: true,
  pt_site_runtime_supported: true,
  pt_site_search_ready: true,
  pt_site_status: '只读搜索已启用',
  avistaz_configured: true,
  avistaz_live_enabled: true,
  avistaz_status: '只读搜索已启用',
  qb_configured: true,
  qb_read_only_enabled: true,
  qb_status: '只读已启用',
  download_control_plane_enabled: false,
  download_executor_enabled: false,
  avistaz_torrent_fetch_enabled: false,
  qb_write_enabled: false,
  download_monitor_enabled: false,
  automation_engine_enabled: false,
}

const mediaItem: MediaItem = {
  id: 'media-1',
  source: 'nextfind',
  source_item_id: 'source-1',
  media_type: 'tv',
  tmdb_id: 42,
  title: '测试剧集',
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
  identity_confidence: 'NEEDS_CONFIRMATION',
  metadata_status: 'PENDING',
  workflow_status: 'IDENTITY_REVIEW',
  discovered_at: '2026-08-11T00:00:00Z',
  updated_at: '2026-08-11T00:00:00Z',
}

const downloadJob: DownloadJob = {
  id: 'job-1',
  execution_id: 'execution-1',
  approval_id: 'approval-1',
  media_item_id: 'media-1',
  status: 'DOWNLOADING',
  release_title: 'Test Series S01E03 1080p WEB-DL',
  info_hash_v1: 'a'.repeat(40),
  info_hash_v2: null,
  save_path_ref: 'media-library',
  category: 'media',
  size_bytes: 1024,
  file_count: 1,
  progress: 0.5,
  download_speed_bps: 100,
  upload_speed_bps: 10,
  downloaded_bytes: 512,
  uploaded_bytes: 50,
  ratio: 0.1,
  hnr_status: 'UNKNOWN',
  started_at: '2026-08-11T00:00:00Z',
  completed_at: null,
  last_seen_at: '2026-08-11T00:01:00Z',
  error_code: null,
  error_message: null,
  created_at: '2026-08-11T00:00:00Z',
  updated_at: '2026-08-11T00:01:00Z',
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve: (value: T) => void = () => undefined
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  mocks.systemStatus.mockResolvedValue(systemStatus)
  mocks.mediaList.mockResolvedValue({ items: [mediaItem], page: 1, page_size: 5, total: 6 })
  mocks.downloadJobList.mockResolvedValue({
    items: [downloadJob],
    page: 1,
    page_size: 5,
    total: 3,
  })
  mocks.discoveryGet.mockResolvedValue({
    id: 'run-1',
    source: 'nextfind',
    status: 'SUCCEEDED',
    started_at: '2026-08-11T00:00:00Z',
    finished_at: '2026-08-11T00:00:01Z',
    discovered_count: 1,
    created_count: 1,
    updated_count: 0,
    error_code: null,
    error_message: null,
    created_at: '2026-08-11T00:00:00Z',
  })
})

describe('dashboard store', () => {
  it('loads independent bounded previews without mutating list-page stores', async () => {
    const store = useDashboardStore()
    await store.refresh()

    expect(mocks.systemStatus).toHaveBeenCalledTimes(1)
    expect(mocks.mediaList).toHaveBeenCalledWith({ page: 1, pageSize: 5 })
    expect(mocks.downloadJobList).toHaveBeenCalledWith({ page: 1, pageSize: 5 })
    expect(store.system?.nextfind_configured).toBe(true)
    expect(store.mediaItems.map((item) => item.id)).toEqual(['media-1'])
    expect(store.mediaTotal).toBe(6)
    expect(store.downloadJobs.map((job) => job.id)).toEqual(['job-1'])
    expect(store.downloadTotal).toBe(3)
  })

  it('keeps successful sections when another dashboard request fails', async () => {
    mocks.systemStatus.mockRejectedValueOnce(new Error('系统状态不可用'))
    mocks.downloadJobList.mockRejectedValueOnce(new Error('下载监控不可用'))
    const store = useDashboardStore()
    await store.refresh()

    expect(store.system).toBeNull()
    expect(store.systemError).toBe('系统状态不可用')
    expect(store.mediaItems).toHaveLength(1)
    expect(store.mediaError).toBeNull()
    expect(store.downloadJobs).toEqual([])
    expect(store.downloadsError).toBe('下载监控不可用')
  })

  it('ignores an older media preview response', async () => {
    const older = deferred<Page<MediaItem>>()
    const newer = deferred<Page<MediaItem>>()
    mocks.mediaList.mockReset().mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise)
    const store = useDashboardStore()

    const first = store.loadMedia()
    const second = store.loadMedia()
    newer.resolve({ items: [{ ...mediaItem, id: 'media-new' }], page: 1, page_size: 5, total: 1 })
    await second
    older.resolve({ items: [{ ...mediaItem, id: 'media-old' }], page: 1, page_size: 5, total: 1 })
    await first

    expect(store.mediaItems.map((item) => item.id)).toEqual(['media-new'])
    expect(store.mediaLoading).toBe(false)
  })

  it('creates a discovery run and refreshes the media preview', async () => {
    mocks.discoveryCreate.mockResolvedValueOnce({
      id: 'run-1',
      source: 'nextfind',
      status: 'PENDING',
      started_at: null,
      finished_at: null,
      discovered_count: 0,
      created_count: 0,
      updated_count: 0,
      error_code: null,
      error_message: null,
      created_at: '2026-08-11T00:00:00Z',
      deduplicated: false,
    })
    const store = useDashboardStore()

    await expect(store.createDiscovery()).resolves.toBe(true)
    expect(mocks.discoveryCreate).toHaveBeenCalledTimes(1)
    expect(mocks.discoveryGet).toHaveBeenCalledWith('run-1', expect.any(AbortSignal))
    expect(mocks.mediaList).toHaveBeenCalledWith({ page: 1, pageSize: 5 })
    expect(store.discoveryNotice).toContain('发现完成')
    expect(store.discoveryRun?.id).toBe('run-1')
    expect(store.discoveryError).toBeNull()
  })
})
