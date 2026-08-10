import { defineStore } from 'pinia'
import { ref } from 'vue'

import { mediaApi, torrentApi } from '../api/client'
import type { MediaItem, TorrentCandidateResult, TorrentSearchRun } from '../types'

export const useTorrentStore = defineStore('torrents', () => {
  const media = ref<MediaItem | null>(null)
  const runs = ref<TorrentSearchRun[]>([])
  const selectedRun = ref<TorrentSearchRun | null>(null)
  const candidates = ref<TorrentCandidateResult[]>([])
  const loading = ref(false)
  const working = ref(false)
  const error = ref<string | null>(null)
  const notice = ref<string | null>(null)
  let generation = 0

  async function load(mediaId: string): Promise<void> {
    const current = ++generation
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
    candidates.value = []
    error.value = null
    try {
      const [run, results] = await Promise.all([
        torrentApi.get(searchId),
        torrentApi.candidates(searchId),
      ])
      selectedRun.value = run
      candidates.value = results
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : '加载搜索结果失败'
    }
  }

  async function create(mediaId: string): Promise<void> {
    working.value = true
    error.value = null
    notice.value = null
    try {
      const result = await torrentApi.create(mediaId, {
        preferred_resolutions: ['2160p', '1080p'],
        preferred_sources: ['BluRay', 'WEB-DL'],
        preferred_audio: [],
        preferred_subtitles: ['Chinese', '中文'],
      })
      await load(mediaId)
      notice.value = result.deduplicated ? '已有只读搜索正在运行' : 'AvistaZ 只读搜索已创建'
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
    working,
    error,
    notice,
    load,
    selectRun,
    create,
  }
})
