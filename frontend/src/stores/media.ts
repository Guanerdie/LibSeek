import { defineStore } from 'pinia'
import { ref } from 'vue'

import { mediaApi } from '../api/client'
import type { MediaItem } from '../types'

export const useMediaStore = defineStore('media', () => {
  const items = ref<MediaItem[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const mediaType = ref('')
  const confidence = ref('')
  const query = ref('')
  const loading = ref(false)
  const error = ref<string | null>(null)
  let generation = 0

  async function load(): Promise<void> {
    const requestGeneration = ++generation
    items.value = []
    total.value = 0
    error.value = null
    loading.value = true
    try {
      const response = await mediaApi.list({
        page: page.value,
        pageSize: pageSize.value,
        mediaType: mediaType.value || undefined,
        confidence: confidence.value || undefined,
        query: query.value || undefined,
      })
      if (requestGeneration === generation) {
        items.value = response.items
        total.value = response.total
      }
    } catch (caught) {
      if (requestGeneration === generation) {
        error.value = caught instanceof Error ? caught.message : '加载影视列表失败'
      }
    } finally {
      if (requestGeneration === generation) loading.value = false
    }
  }

  async function applyFilters(): Promise<void> {
    page.value = 1
    await load()
  }

  async function previousPage(): Promise<void> {
    if (page.value <= 1) return
    page.value -= 1
    await load()
  }

  async function nextPage(): Promise<void> {
    if (page.value * pageSize.value >= total.value) return
    page.value += 1
    await load()
  }

  return {
    items,
    total,
    page,
    pageSize,
    mediaType,
    confidence,
    query,
    loading,
    error,
    load,
    applyFilters,
    previousPage,
    nextPage,
  }
})

