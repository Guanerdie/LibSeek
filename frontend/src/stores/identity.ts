import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { mediaApi, torrentApi } from '../api/client'
import type {
  JobStatus,
  MediaItem,
  MetadataMatch,
  MetadataResolutionJob,
  TorrentSearchRun,
} from '../types'
import { hasConfirmedIdentityEvidence } from '../utils/identity'
import { isAbortError, waitForPoll } from '../utils/polling'

const RESOLUTION_POLL_INTERVAL_MS = 1_500
const RESOLUTION_POLL_MAX_ATTEMPTS = 80
const RESOLUTION_ACTIVE_STATUSES = new Set<JobStatus>(['PENDING', 'RUNNING', 'RETRY_WAIT'])

export const useIdentityStore = defineStore('identity', () => {
  const media = ref<MediaItem | null>(null)
  const candidates = ref<MetadataMatch[]>([])
  const runs = ref<TorrentSearchRun[]>([])
  const loading = ref(false)
  const working = ref(false)
  const error = ref<string | null>(null)
  const notice = ref<string | null>(null)
  const resolutionJobId = ref<string | null>(null)
  let generation = 0
  let actionGeneration = 0
  let activeMediaId: string | null = null
  let resolutionController: AbortController | null = null
  const identityGatePassed = computed(() =>
    hasConfirmedIdentityEvidence(media.value, runs.value),
  )

  async function loadState(mediaId: string, invalidateActions: boolean): Promise<void> {
    if (invalidateActions) {
      cancelResolution()
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

  function cancelResolution(): void {
    actionGeneration += 1
    resolutionController?.abort()
    resolutionController = null
    resolutionJobId.value = null
    working.value = false
  }

  function resolutionSuccessNotice(): string {
    if (identityGatePassed.value) return 'TMDB 解析已完成，影视身份已经确认。'
    if (candidates.value.length > 0) return `TMDB 解析已完成，找到 ${candidates.value.length} 个候选，请人工确认。`
    return 'TMDB 解析任务已成功，未返回可确认候选。'
  }

  function resolutionFailure(job: MetadataResolutionJob): string {
    const detail = job.error_message?.trim()
      || (job.status === 'CANCELLED' ? 'TMDB 解析任务已取消' : 'TMDB 解析任务失败')
    return job.error_code ? `${job.error_code} · ${detail}` : detail
  }

  async function resolve(mediaId: string): Promise<boolean> {
    if (activeMediaId !== mediaId || media.value?.id !== mediaId) {
      error.value = '当前影视身份已变更，请等待页面刷新完成'
      return false
    }
    cancelResolution()
    const currentAction = ++actionGeneration
    const expectedGeneration = generation
    const controller = new AbortController()
    resolutionController = controller
    working.value = true
    error.value = null
    notice.value = null
    try {
      const response = await mediaApi.resolve(mediaId)
      if (
        currentAction !== actionGeneration ||
        expectedGeneration !== generation ||
        activeMediaId !== mediaId
      ) return false
      if (response.media_id !== mediaId || !response.job_id?.trim()) {
        throw new Error('TMDB 解析任务响应与当前影视不一致，请刷新后重试')
      }
      resolutionJobId.value = response.job_id
      notice.value = response.deduplicated
        ? '已有 TMDB 解析任务正在运行，正在等待结果。'
        : 'TMDB 解析任务已创建，正在等待结果。'

      let lastStatus: JobStatus | null = null
      for (let attempt = 0; attempt < RESOLUTION_POLL_MAX_ATTEMPTS; attempt += 1) {
        if (attempt > 0) await waitForPoll(RESOLUTION_POLL_INTERVAL_MS, controller.signal)
        const job = await mediaApi.resolveJob(mediaId, response.job_id, controller.signal)
        if (currentAction !== actionGeneration || activeMediaId !== mediaId) return false
        if (job.media_id !== mediaId || job.job_id !== response.job_id) {
          throw new Error('TMDB 解析任务状态响应绑定不一致，请刷新后重试')
        }
        lastStatus = job.status
        if (job.status === 'SUCCEEDED') {
          const [mediaResponse, candidateResponse] = await Promise.all([
            mediaApi.get(mediaId, controller.signal),
            mediaApi.metadataCandidates(mediaId, controller.signal),
          ])
          if (
            currentAction !== actionGeneration ||
            expectedGeneration !== generation ||
            activeMediaId !== mediaId
          ) return false
          if (
            mediaResponse.id !== mediaId ||
            candidateResponse.some((candidate) => candidate.media_id !== mediaId)
          ) {
            throw new Error('TMDB 解析结果与当前影视不一致，请刷新后重试')
          }
          media.value = mediaResponse
          candidates.value = candidateResponse
          notice.value = resolutionSuccessNotice()
          return true
        }
        if (job.status === 'FAILED' || job.status === 'CANCELLED') {
          notice.value = null
          error.value = resolutionFailure(job)
          return false
        }
        if (!RESOLUTION_ACTIVE_STATUSES.has(job.status)) {
          throw new Error(`TMDB 解析任务返回未知状态：${job.status}`)
        }
        notice.value = `TMDB 解析任务状态：${job.status}（${attempt + 1}/${RESOLUTION_POLL_MAX_ATTEMPTS}）…`
      }

      notice.value = `等待 TMDB 解析任务已超时；最后任务状态：${lastStatus ?? '未知'}。任务仍可能在后台继续，可稍后刷新。`
      return false
    } catch (caught) {
      if (
        currentAction === actionGeneration &&
        activeMediaId === mediaId &&
        !isAbortError(caught)
      ) {
        error.value = caught instanceof Error ? caught.message : '创建解析任务失败'
      }
      return false
    } finally {
      if (currentAction === actionGeneration) {
        working.value = false
        resolutionController = null
      }
    }
  }

  async function confirm(mediaId: string, metadataMatchId: string): Promise<void> {
    if (activeMediaId !== mediaId || media.value?.id !== mediaId) {
      error.value = '当前影视身份已变更，请等待页面刷新完成'
      return
    }
    cancelResolution()
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
    resolutionJobId,
    load,
    resolve,
    confirm,
    cancelResolution,
  }
})
