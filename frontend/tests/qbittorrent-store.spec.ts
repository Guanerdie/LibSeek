import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useQbittorrentStore } from '../src/stores/qbittorrent'

const mocks = vi.hoisted(() => ({
  status: vi.fn(),
  torrents: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  qbApi: { status: mocks.status, torrents: mocks.torrents },
}))

const status = {
  connected: true,
  application_version: 'v5.0.4',
  web_api_version: '2.11.4',
  torrent_count: 1,
  category_count: 1,
  active_seeding_count: 1,
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('qBittorrent read-only store', () => {
  it('clears old data when a later request fails', async () => {
    mocks.status.mockResolvedValueOnce(status).mockRejectedValueOnce(new Error('连接失败'))
    mocks.torrents.mockResolvedValueOnce({ items: [{ hash: 'a'.repeat(40) }], total: 1 })
    mocks.torrents.mockRejectedValueOnce(new Error('连接失败'))
    const store = useQbittorrentStore()
    await store.load()
    expect(store.status?.application_version).toBe('v5.0.4')
    expect(store.torrents).toHaveLength(1)

    await store.load()
    expect(store.status).toBeNull()
    expect(store.torrents).toEqual([])
    expect(store.error).toBe('连接失败')
  })

  it('ignores a response after its request generation is invalidated', async () => {
    let resolveStatus: (value: typeof status) => void = () => undefined
    let resolveTorrents: (value: { items: never[]; total: number }) => void = () => undefined
    mocks.status.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveStatus = resolve
      }),
    )
    mocks.torrents.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveTorrents = resolve
      }),
    )
    const store = useQbittorrentStore()
    const pending = store.load()
    store.invalidate()
    resolveStatus(status)
    resolveTorrents({ items: [], total: 0 })
    await pending
    expect(store.status).toBeNull()
    expect(store.torrents).toEqual([])
  })
})
