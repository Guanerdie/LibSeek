import { defineStore } from 'pinia'
import { ref } from 'vue'

import { qbApi } from '../api/client'
import type { QbStatus, QbTorrent } from '../types'

export const useQbittorrentStore = defineStore('qbittorrent', () => {
  const status = ref<QbStatus | null>(null)
  const torrents = ref<QbTorrent[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  let generation = 0

  async function load(): Promise<void> {
    const current = ++generation
    status.value = null
    torrents.value = []
    error.value = null
    loading.value = true
    try {
      const [statusResult, torrentResult] = await Promise.all([qbApi.status(), qbApi.torrents()])
      if (current === generation) {
        status.value = statusResult
        torrents.value = torrentResult.items
      }
    } catch (caught) {
      if (current === generation) {
        status.value = null
        torrents.value = []
        error.value = caught instanceof Error ? caught.message : 'qBittorrent 只读连接失败'
      }
    } finally {
      if (current === generation) loading.value = false
    }
  }

  function invalidate(): void {
    generation += 1
    status.value = null
    torrents.value = []
    error.value = null
    loading.value = false
  }

  return { status, torrents, loading, error, load, invalidate }
})
