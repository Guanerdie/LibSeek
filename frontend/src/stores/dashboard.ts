import { defineStore } from 'pinia'
import { ref } from 'vue'

import { discoveryApi, downloadJobApi, mediaApi, systemApi } from '../api/client'
import type { DiscoveryRun, DownloadJob, MediaItem, SystemStatus } from '../types'
import { isAbortError, waitForPoll } from '../utils/polling'

const DISCOVERY_POLL_INTERVAL_MS = 1_500
const DISCOVERY_POLL_MAX_ATTEMPTS = 80
const DISCOVERY_TERMINAL_STATUSES = new Set<DiscoveryRun['status']>([
  'SUCCEEDED',
  'FAILED',
  'CANCELLED',
])

type DiscoveryNoticeKind = 'working' | 'success' | 'warning'

export const useDashboardStore = defineStore('dashboard', () => {
  const system = ref<SystemStatus | null>(null)
  const mediaItems = ref<MediaItem[]>([])
  const mediaTotal = ref(0)
  const downloadJobs = ref<DownloadJob[]>([])
  const downloadTotal = ref(0)

  const systemLoading = ref(false)
  const mediaLoading = ref(false)
  const downloadsLoading = ref(false)
  const discoveryCreating = ref(false)

  const systemError = ref<string | null>(null)
  const mediaError = ref<string | null>(null)
  const downloadsError = ref<string | null>(null)
  const discoveryError = ref<string | null>(null)
  const discoveryNotice = ref<string | null>(null)
  const discoveryNoticeKind = ref<DiscoveryNoticeKind>('working')
  const discoveryRun = ref<DiscoveryRun | null>(null)

  let systemGeneration = 0
  let mediaGeneration = 0
  let downloadsGeneration = 0
  let discoveryGeneration = 0
  let discoveryController: AbortController | null = null

  async function loadSystem(): Promise<void> {
    const current = ++systemGeneration
    system.value = null
    systemError.value = null
    systemLoading.value = true
    try {
      const result = await systemApi.status()
      if (current === systemGeneration) system.value = result
    } catch (caught) {
      if (current === systemGeneration) {
        systemError.value = caught instanceof Error ? caught.message : '加载系统状态失败'
      }
    } finally {
      if (current === systemGeneration) systemLoading.value = false
    }
  }

  async function loadMedia(): Promise<void> {
    const current = ++mediaGeneration
    mediaItems.value = []
    mediaTotal.value = 0
    mediaError.value = null
    mediaLoading.value = true
    try {
      const result = await mediaApi.list({ page: 1, pageSize: 5 })
      if (current === mediaGeneration) {
        mediaItems.value = result.items
        mediaTotal.value = result.total
      }
    } catch (caught) {
      if (current === mediaGeneration) {
        mediaError.value = caught instanceof Error ? caught.message : '加载未入库影视预览失败'
      }
    } finally {
      if (current === mediaGeneration) mediaLoading.value = false
    }
  }

  async function loadDownloads(): Promise<void> {
    const current = ++downloadsGeneration
    downloadJobs.value = []
    downloadTotal.value = 0
    downloadsError.value = null
    downloadsLoading.value = true
    try {
      const result = await downloadJobApi.list({ page: 1, pageSize: 5 })
      if (current === downloadsGeneration) {
        downloadJobs.value = result.items
        downloadTotal.value = result.total
      }
    } catch (caught) {
      if (current === downloadsGeneration) {
        downloadsError.value = caught instanceof Error ? caught.message : '加载下载任务预览失败'
      }
    } finally {
      if (current === downloadsGeneration) downloadsLoading.value = false
    }
  }

  async function refresh(): Promise<void> {
    await Promise.all([loadSystem(), loadMedia(), loadDownloads()])
  }

  function cancelDiscoveryPolling(): void {
    discoveryGeneration += 1
    discoveryController?.abort()
    discoveryController = null
    discoveryCreating.value = false
  }

  function discoveryFailure(run: DiscoveryRun): string {
    const detail = run.error_message?.trim() || '发现任务未完成'
    return run.error_code ? `${run.error_code} · ${detail}` : detail
  }

  async function createDiscovery(): Promise<boolean> {
    cancelDiscoveryPolling()
    const current = ++discoveryGeneration
    const controller = new AbortController()
    discoveryController = controller
    discoveryCreating.value = true
    discoveryError.value = null
    discoveryNotice.value = null
    discoveryNoticeKind.value = 'working'
    discoveryRun.value = null
    try {
      const result = await discoveryApi.create()
      if (current !== discoveryGeneration) return false
      discoveryRun.value = result
      discoveryNotice.value = result.deduplicated
        ? '发现任务已在运行，正在等待处理结果。'
        : '发现任务已创建，正在等待 NextFind 处理结果。'

      let latest = result
      for (
        let attempt = 0;
        attempt < DISCOVERY_POLL_MAX_ATTEMPTS && !DISCOVERY_TERMINAL_STATUSES.has(latest.status);
        attempt += 1
      ) {
        if (attempt > 0) await waitForPoll(DISCOVERY_POLL_INTERVAL_MS, controller.signal)
        latest = await discoveryApi.get(result.id, controller.signal)
        if (latest.id !== result.id) throw new Error('发现任务响应身份不一致，请前往任务页确认')
        if (current !== discoveryGeneration) return false
        discoveryRun.value = latest
      }

      if (!DISCOVERY_TERMINAL_STATUSES.has(latest.status)) {
        discoveryNoticeKind.value = 'warning'
        discoveryNotice.value = '等待发现结果已超时；任务可能仍在后台运行，请前往发现任务查看进度。'
        return true
      }
      if (latest.status !== 'SUCCEEDED') {
        discoveryNotice.value = null
        discoveryError.value = discoveryFailure(latest)
        return false
      }

      await loadMedia()
      if (current !== discoveryGeneration) return false
      discoveryNoticeKind.value = 'success'
      discoveryNotice.value = `发现完成：新增 ${latest.created_count} 项，更新 ${latest.updated_count} 项。`
      return true
    } catch (caught) {
      if (current === discoveryGeneration && !isAbortError(caught)) {
        discoveryError.value = caught instanceof Error ? caught.message : '创建发现任务失败'
      }
      return false
    } finally {
      if (current === discoveryGeneration) {
        discoveryCreating.value = false
        discoveryController = null
      }
    }
  }

  return {
    system,
    mediaItems,
    mediaTotal,
    downloadJobs,
    downloadTotal,
    systemLoading,
    mediaLoading,
    downloadsLoading,
    discoveryCreating,
    systemError,
    mediaError,
    downloadsError,
    discoveryError,
    discoveryNotice,
    discoveryNoticeKind,
    discoveryRun,
    loadSystem,
    loadMedia,
    loadDownloads,
    refresh,
    createDiscovery,
    cancelDiscoveryPolling,
  }
})
