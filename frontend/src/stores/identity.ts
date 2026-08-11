import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { mediaApi, torrentApi } from '../api/client'
import type { MediaItem, MetadataMatch, TorrentSearchRun } from '../types'
import { hasConfirmedIdentityEvidence } from '../utils/identity'

export const useIdentityStore = defineStore('identity', () => {
  const media = ref<MediaItem | null>(null)
  const candidates = ref<MetadataMatch[]>([])
  const runs = ref<TorrentSearchRun[]>([])
  const loading = ref(false)
  const working = ref(false)
  const error = ref<string | null>(null)
  const notice = ref<string | null>(null)
  let generation = 0
  let actionGeneration = 0
  let activeMediaId: string | null = null
  const identityGatePassed = computed(() =>
    hasConfirmedIdentityEvidence(media.value, runs.value),
  )

  async function loadState(mediaId: string, invalidateActions: boolean): Promise<void> {
    if (invalidateActions) {
      actionGeneration += 1
      working.value = false
    }
    const current = ++generation
    activeMediaId = mediaId
    media.value = null
    candidates.value = []
    runs.value = []
    error.value = null
    notice.value = null
    loading.value = true
    try {
      const [mediaResponse, candidateResponse, runResponse] = await Promise.all([
        mediaApi.get(mediaId),
        mediaApi.metadataCandidates(mediaId),
        torrentApi.list(mediaId),
      ])
      const runIds = new Set(runResponse.map((run) => run.id))
      if (
        mediaResponse.id !== mediaId ||
        candidateResponse.some((candidate) => candidate.media_id !== mediaId) ||
        runIds.size !== runResponse.length ||
        runResponse.some((run) => run.media_id !== mediaId)
      ) {
        throw new Error('身份候选或历史 PT 搜索响应身份不一致，请刷新后重试')
      }
      if (current === generation) {
        media.value = mediaResponse
        candidates.value = candidateResponse
        runs.value = runResponse
      }
    } catch (caught) {
      if (current === generation) {
        error.value = caught instanceof Error ? caught.message : '加载身份候选失败'
      }
    } finally {
      if (current === generation) loading.value = false
    }
  }

  const load = (mediaId: string) => loadState(mediaId, true)

  async function resolve(mediaId: string): Promise<void> {
    if (activeMediaId !== mediaId || media.value?.id !== mediaId) {
      error.value = '当前影视身份已变更，请等待页面刷新完成'
      return
    }
    const currentAction = ++actionGeneration
    const expectedGeneration = generation
    working.value = true
    error.value = null
    notice.value = null
    try {
      const response = await mediaApi.resolve(mediaId)
      if (
        currentAction !== actionGeneration ||
        expectedGeneration !== generation ||
        activeMediaId !== mediaId
      ) return
      await loadState(mediaId, false)
      if (currentAction !== actionGeneration || activeMediaId !== mediaId) return
      notice.value = response.deduplicated
        ? '已有解析任务正在运行；可点击“刷新候选”查看 Worker 的最新结果'
        : '只读 TMDB 解析任务已创建；任务异步执行，可点击“刷新候选”查看最新结果'
    } catch (caught) {
      if (currentAction === actionGeneration && activeMediaId === mediaId) {
        error.value = caught instanceof Error ? caught.message : '创建解析任务失败'
      }
    } finally {
      if (currentAction === actionGeneration) working.value = false
    }
  }

  async function confirm(mediaId: string, metadataMatchId: string): Promise<void> {
    if (activeMediaId !== mediaId || media.value?.id !== mediaId) {
      error.value = '当前影视身份已变更，请等待页面刷新完成'
      return
    }
    const currentAction = ++actionGeneration
    const expectedGeneration = generation
    working.value = true
    error.value = null
    notice.value = null
    try {
      await mediaApi.confirmIdentity(mediaId, metadataMatchId)
      if (
        currentAction !== actionGeneration ||
        expectedGeneration !== generation ||
        activeMediaId !== mediaId
      ) return
      await loadState(mediaId, false)
      if (currentAction !== actionGeneration || activeMediaId !== mediaId) return
      notice.value = '影视身份已由人工确认'
    } catch (caught) {
      if (currentAction === actionGeneration && activeMediaId === mediaId) {
        error.value = caught instanceof Error ? caught.message : '提交人工确认失败'
      }
    } finally {
      if (currentAction === actionGeneration) working.value = false
    }
  }

  return {
    media,
    candidates,
    runs,
    identityGatePassed,
    loading,
    working,
    error,
    notice,
    load,
    resolve,
    confirm,
  }
})
