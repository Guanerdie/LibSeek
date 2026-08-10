import { defineStore } from 'pinia'
import { ref } from 'vue'

import { systemApi } from '../api/client'
import type { SystemStatus } from '../types'

export const useSystemStore = defineStore('system', () => {
  const data = ref<SystemStatus | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let generation = 0

  async function refresh(): Promise<void> {
    const requestGeneration = ++generation
    data.value = null
    error.value = null
    loading.value = true
    try {
      const response = await systemApi.status()
      if (requestGeneration === generation) data.value = response
    } catch (caught) {
      if (requestGeneration === generation) {
        error.value = caught instanceof Error ? caught.message : '加载系统状态失败'
      }
    } finally {
      if (requestGeneration === generation) loading.value = false
    }
  }

  return { data, loading, error, refresh }
})

