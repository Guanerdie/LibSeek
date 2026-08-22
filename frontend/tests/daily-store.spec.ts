import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useDailyStore } from '../src/stores/daily'
import type { DailyDownload, DailyMedia } from '../src/types'

const mocks = vi.hoisted(() => ({
  syncMedia: vi.fn(),
  media: vi.fn(),
  mediaDetail: vi.fn(),
  identify: vi.fn(),
  createSearch: vi.fn(),
  search: vi.fn(),
  downloadCandidate: vi.fn(),
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
  year: 2026,
  poster_path: null,
  state: 'READY',
  attention_reason: null,
  discovered_at: '2026-08-22T00:00:00Z',
  updated_at: '2026-08-22T00:00:00Z',
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

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('simplified daily store', () => {
  it('syncs NextFind and immediately refreshes the missing list', async () => {
    mocks.syncMedia.mockResolvedValueOnce({ created: 1, updated: 0 })
    mocks.media.mockResolvedValueOnce({ items: [media], page: 1, page_size: 30, total: 1 })
    const store = useDailyStore()

    await store.syncMedia()

    expect(mocks.syncMedia).toHaveBeenCalledOnce()
    expect(store.media).toEqual([media])
  })

  it('passes the single warning confirmation directly to download submission', async () => {
    mocks.downloadCandidate.mockResolvedValueOnce(download)
    const store = useDailyStore()

    expect(await store.download('candidate-1', true)).toBe(true)
    expect(mocks.downloadCandidate).toHaveBeenCalledWith('candidate-1', true)
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
