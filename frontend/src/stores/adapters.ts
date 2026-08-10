import { defineStore } from 'pinia'
import { ref } from 'vue'

import { adapterApi } from '../api/client'
import type { AdapterManifest } from '../types'

export const useAdaptersStore = defineStore('adapters', () => {
  const items = ref<AdapterManifest[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  let generation = 0

  async function load(): Promise<void> {
    const requestGeneration = ++generation
    items.value = []
    error.value = null
    loading.value = true
    try {
      const response = await adapterApi.list()
      if (requestGeneration === generation) items.value = response
    } catch (caught) {
      if (requestGeneration === generation) {
        error.value = caught instanceof Error ? caught.message : '加载适配器失败'
      }
    } finally {
      if (requestGeneration === generation) loading.value = false
    }
  }

  return { items, loading, error, load }
})

