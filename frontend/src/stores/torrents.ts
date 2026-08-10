import { defineStore } from 'pinia'
import { ref } from 'vue'

import { mediaApi, torrentApi } from '../api/client'
import type {
  MediaItem,
  TorrentCandidateResult,
  TorrentSearchPreferences,
  TorrentSearchRun,
} from '../types'

export const useTorrentStore = defineStore('torrents', () => {
  const media = ref<MediaItem | null>(null)
  const runs = ref<TorrentSearchRun[]>([])
  const selectedRun = ref<TorrentSearchRun | null>(null)
  const candidates = ref<TorrentCandidateResult[]>([])
  const loading = ref(false)
  const selecting = ref(false)
  const working = ref(false)
  const error = ref<string | null>(null)
  const notice = ref<string | null>(null)
  let generation = 0
  let selectionGeneration = 0
  let selectionController: AbortController | null = null

  function cancelSelection(): void {
    selectionGeneration += 1
    selectionController?.abort()
    selectionController = null
    selecting.value = false
  }

  async function load(mediaId: string): Promise<void> {
    const current = ++generation
    cancelSelection()
    media.value = null
    runs.value = []
    selectedRun.value = null
    candidates.value = []
    loading.value = true
    error.value = null
    notice.value = null
    try {
      const [mediaResponse, runResponse] = await Promise.all([
        mediaApi.get(mediaId),
        torrentApi.list(mediaId),
      ])
      if (current !== generation) return
      media.value = mediaResponse
      runs.value = runResponse
      if (runResponse[0]) await selectRun(runResponse[0].id)
    } catch (caught) {
      if (current === generation) {
        error.value = caught instanceof Error ? caught.message : '加载 PT 候选失败'
      }
    } finally {
      if (current === generation) loading.value = false
    }
  }

  async function selectRun(searchId: string): Promise<void> {
    const current = ++selectionGeneration
    selectionController?.abort()
    const controller = new AbortController()
    selectionController = controller
    candidates.value = []
    error.value = null
    selecting.value = true
    try {
      const [run, results] = await Promise.all([
        torrentApi.get(searchId, controller.signal),
        torrentApi.candidates(searchId, controller.signal),
      ])
      if (current !== selectionGeneration) return
      selectedRun.value = run
      candidates.value = results
    } catch (caught) {
      if (current === selectionGeneration && !(caught instanceof Error && caught.name === 'AbortError')) {
        error.value = caught instanceof Error ? caught.message : '加载搜索结果失败'
      }
    } finally {
      if (current === selectionGeneration) {
        selecting.value = false
        selectionController = null
      }
    }
  }

  async function create(mediaId: string, preferences: TorrentSearchPreferences): Promise<void> {
    working.value = true
    error.value = null
    notice.value = null
    try {
      const result = await torrentApi.create(mediaId, preferences)
      await load(mediaId)
      notice.value = result.deduplicated
        ? '已有只读搜索正在运行；可点击“刷新结果”查看 Worker 的最新结果'
        : 'AvistaZ 只读搜索已创建；任务异步执行，可点击“刷新结果”查看最新结果'
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : '创建 PT 搜索失败'
    } finally {
      working.value = false
    }
  }

  return {
    media,
    runs,
    selectedRun,
    candidates,
    loading,
    selecting,
    working,
    error,
    notice,
    load,
    selectRun,
    cancelSelection,
    create,
  }
})
