import { defineStore } from 'pinia'
import { ref } from 'vue'

import { discoveryApi, mediaApi } from '../api/client'
import type { DiscoveryRun, MediaItem } from '../types'
import type { MediaRegion } from '../utils/mediaRegions'
import { isAbortError, waitForPoll } from '../utils/polling'

const SYNC_POLL_INTERVAL_MS = 1_500
const SYNC_POLL_MAX_ATTEMPTS = 80
const FOLLOW_UP_POLL_MAX_ATTEMPTS = 40
const SYNC_TERMINAL_STATUSES = new Set<DiscoveryRun['status']>([
  'SUCCEEDED',
  'FAILED',
  'CANCELLED',
])
const FOLLOW_UP_ACTIVE_STATUSES = new Set<MediaItem['workflow_status']>([
  'DISCOVERED',
  'METADATA_PENDING',
  'IDENTITY_CONFIRMED',
  'PT_SEARCH_PENDING',
  'PT_SEARCHING',
])

interface LoadOptions {
  signal?: AbortSignal
  preserveItems?: boolean
  syncRequestGeneration?: number
}

export const useMediaStore = defineStore('media', () => {
  const items = ref<MediaItem[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const mediaType = ref('')
  const confidence = ref('')
  const region = ref<MediaRegion | ''>('')
  const query = ref('')
  const discoveryStatus = ref<'MISSING' | 'IN_LIBRARY' | 'ALL'>('MISSING')
  const loading = ref(false)
  const syncing = ref(false)
  const error = ref<string | null>(null)
  const syncError = ref<string | null>(null)
  const syncNotice = ref<string | null>(null)
  let generation = 0
  let syncGeneration = 0
  let syncController: AbortController | null = null

  async function load(options: LoadOptions = {}): Promise<boolean> {
    const requestGeneration = ++generation
    const belongsToCurrentSync = () =>
      options.syncRequestGeneration === undefined
      || options.syncRequestGeneration === syncGeneration
    if (!options.preserveItems) {
      items.value = []
      total.value = 0
    }
    error.value = null
    if (!options.preserveItems) loading.value = true
    try {
      const params = {
        page: page.value,
        pageSize: pageSize.value,
        mediaType: mediaType.value || undefined,
        confidence: confidence.value || undefined,
        region: region.value || undefined,
        query: query.value || undefined,
        discoveryStatus: discoveryStatus.value,
      }
      const response = options.signal
        ? await mediaApi.list(params, options.signal)
        : await mediaApi.list(params)
      if (requestGeneration === generation && belongsToCurrentSync()) {
        items.value = response.items
        total.value = response.total
        return true
      }
    } catch (caught) {
      if (
        requestGeneration === generation
        && belongsToCurrentSync()
        && !isAbortError(caught)
      ) {
        error.value = caught instanceof Error ? caught.message : '加载影视列表失败'
      }
    } finally {
      if (requestGeneration === generation && !options.preserveItems) loading.value = false
    }
    return false
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

  function cancelSync(): void {
    syncGeneration += 1
    syncController?.abort()
    syncController = null
    syncing.value = false
  }

  function trackedItemsStillActive(trackedIds: ReadonlySet<string>): boolean {
    return items.value.some(
      (item) => trackedIds.has(item.id) && FOLLOW_UP_ACTIVE_STATUSES.has(item.workflow_status),
    )
  }

  async function trackFollowUpWork(
    requestGeneration: number,
    controller: AbortController,
  ): Promise<'completed' | 'timed_out' | 'cancelled'> {
    const trackedIds = new Set(
      items.value
        .filter((item) => FOLLOW_UP_ACTIVE_STATUSES.has(item.workflow_status))
        .map((item) => item.id),
    )
    if (trackedIds.size === 0) return 'completed'

    for (let attempt = 0; attempt < FOLLOW_UP_POLL_MAX_ATTEMPTS; attempt += 1) {
      await waitForPoll(SYNC_POLL_INTERVAL_MS, controller.signal)
      await load({
        signal: controller.signal,
        preserveItems: true,
        syncRequestGeneration: requestGeneration,
      })
      if (requestGeneration !== syncGeneration) return 'cancelled'
      if (!trackedItemsStillActive(trackedIds)) return 'completed'
    }
    return 'timed_out'
  }

  async function syncNextFind(): Promise<boolean> {
    cancelSync()
    const requestGeneration = ++syncGeneration
    const controller = new AbortController()
    syncController = controller
    syncing.value = true
    syncError.value = null
    syncNotice.value = null
    try {
      const accepted = await discoveryApi.create()
      if (requestGeneration !== syncGeneration) return false
      syncNotice.value = accepted.deduplicated
        ? '同步任务已在运行，正在等待最新结果。'
        : '正在从 NextFind 同步未入库影视。'

      let run = accepted
      for (
        let attempt = 0;
        attempt < SYNC_POLL_MAX_ATTEMPTS && !SYNC_TERMINAL_STATUSES.has(run.status);
        attempt += 1
      ) {
        if (attempt > 0) await waitForPoll(SYNC_POLL_INTERVAL_MS, controller.signal)
        run = await discoveryApi.get(accepted.id, controller.signal)
        if (run.id !== accepted.id) throw new Error('NextFind 同步响应不一致，请重新同步')
        if (requestGeneration !== syncGeneration) return false
      }

      if (!SYNC_TERMINAL_STATUSES.has(run.status)) {
        syncNotice.value = '同步仍在后台运行，可稍后刷新列表查看结果。'
        return true
      }
      if (run.status !== 'SUCCEEDED') {
        const detail = run.error_message?.trim() || 'NextFind 同步未完成'
        syncError.value = run.error_code ? `${run.error_code} · ${detail}` : detail
        syncNotice.value = null
        return false
      }

      const summary = `同步完成：新增 ${run.created_count} 项，更新 ${run.updated_count} 项。`
      await load({
        signal: controller.signal,
        preserveItems: true,
        syncRequestGeneration: requestGeneration,
      })
      if (requestGeneration !== syncGeneration) return false
      if (items.value.some((item) => FOLLOW_UP_ACTIVE_STATUSES.has(item.workflow_status))) {
        syncNotice.value = `${summary}正在更新身份识别和 PT 搜索状态。`
      }
      const followUpStatus = await trackFollowUpWork(requestGeneration, controller)
      if (followUpStatus === 'cancelled' || requestGeneration !== syncGeneration) return false
      syncNotice.value = followUpStatus === 'timed_out'
        ? `${summary}后续处理仍在后台运行，请稍后刷新列表。`
        : summary
      return true
    } catch (caught) {
      if (requestGeneration === syncGeneration && !isAbortError(caught)) {
        syncError.value = caught instanceof Error ? caught.message : 'NextFind 同步失败'
        syncNotice.value = null
      }
      return false
    } finally {
      if (requestGeneration === syncGeneration) {
        syncing.value = false
        syncController = null
      }
    }
  }

  return {
    items,
    total,
    page,
    pageSize,
    mediaType,
    confidence,
    region,
    query,
    discoveryStatus,
    loading,
    syncing,
    error,
    syncError,
    syncNotice,
    load,
    applyFilters,
    previousPage,
    nextPage,
    syncNextFind,
    cancelSync,
  }
})
