import { defineStore } from 'pinia'
import { ref } from 'vue'

import { mediaApi } from '../api/client'
import type { MediaItem, MetadataMatch } from '../types'

export const useIdentityStore = defineStore('identity', () => {
  const media = ref<MediaItem | null>(null)
  const candidates = ref<MetadataMatch[]>([])
  const loading = ref(false)
  const working = ref(false)
  const error = ref<string | null>(null)
  const notice = ref<string | null>(null)
  let generation = 0

  async function load(mediaId: string): Promise<void> {
    const current = ++generation
    media.value = null
    candidates.value = []
    error.value = null
    notice.value = null
    loading.value = true
    try {
      const [mediaResponse, candidateResponse] = await Promise.all([
        mediaApi.get(mediaId),
        mediaApi.metadataCandidates(mediaId),
      ])
      if (current === generation) {
        media.value = mediaResponse
        candidates.value = candidateResponse
      }
    } catch (caught) {
      if (current === generation) {
        error.value = caught instanceof Error ? caught.message : '加载身份候选失败'
      }
    } finally {
      if (current === generation) loading.value = false
    }
  }

  async function resolve(mediaId: string): Promise<void> {
    working.value = true
    error.value = null
    notice.value = null
    try {
      const response = await mediaApi.resolve(mediaId)
      await load(mediaId)
      notice.value = response.deduplicated
        ? '已有解析任务正在运行；可点击“刷新候选”查看 Worker 的最新结果'
        : '只读 TMDB 解析任务已创建；任务异步执行，可点击“刷新候选”查看最新结果'
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : '创建解析任务失败'
    } finally {
      working.value = false
    }
  }

  async function confirm(mediaId: string, metadataMatchId: string): Promise<void> {
    working.value = true
    error.value = null
    notice.value = null
    try {
      await mediaApi.confirmIdentity(mediaId, metadataMatchId)
      await load(mediaId)
      notice.value = '影视身份已由人工确认'
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : '提交人工确认失败'
    } finally {
      working.value = false
    }
  }

  return { media, candidates, loading, working, error, notice, load, resolve, confirm }
})
