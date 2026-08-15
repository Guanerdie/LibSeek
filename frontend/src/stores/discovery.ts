import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { discoveryApi } from '../api/client'
import type { DiscoveryRun } from '../types'
import { isAbortError, waitForPoll } from '../utils/polling'

const DISCOVERY_POLL_INTERVAL_MS = 1_500
const DISCOVERY_ACTIVE_STATUSES = new Set<DiscoveryRun['status']>([
  'PENDING',
  'RUNNING',
  'RETRY_WAIT',
])

interface LoadOptions {
  background?: boolean
  signal?: AbortSignal
  pollingGeneration?: number
}

interface DetailOptions {
  preserveSelection?: boolean
  signal?: AbortSignal
  pollingGeneration?: number
}

export const useDiscoveryStore = defineStore('discovery', () => {
  const runs = ref<DiscoveryRun[]>([])
  const selected = ref<DiscoveryRun | null>(null)
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const loading = ref(false)
  const creating = ref(false)
  const autoRefreshing = ref(false)
  const error = ref<string | null>(null)
  const notice = ref<string | null>(null)
  const activeRunCount = computed(
    () => runs.value.filter((run) => DISCOVERY_ACTIVE_STATUSES.has(run.status)).length,
  )
  const retryWaitingCount = computed(
    () => runs.value.filter((run) => run.status === 'RETRY_WAIT').length,
  )

  let listGeneration = 0
  let detailGeneration = 0
  let pollingGeneration = 0
  let detailController: AbortController | null = null
  let pollingController: AbortController | null = null
  let pollingTask: Promise<void> | null = null

  function pollingRequestIsCurrent(
    expectedGeneration: number | undefined,
    signal: AbortSignal | undefined,
  ): boolean {
    return !signal?.aborted
      && (expectedGeneration === undefined || expectedGeneration === pollingGeneration)
  }

  async function loadRuns(options: LoadOptions = {}): Promise<boolean> {
    const requestGeneration = ++listGeneration
    const isBackground = options.background === true
    if (!isBackground) loading.value = true
    error.value = null
    try {
      const response = await discoveryApi.list(page.value, pageSize.value, options.signal)
      if (
        requestGeneration !== listGeneration
        || !pollingRequestIsCurrent(options.pollingGeneration, options.signal)
      ) return false

      runs.value = response.items
      total.value = response.total
      return true
    } catch (caught) {
      if (
        requestGeneration === listGeneration
        && pollingRequestIsCurrent(options.pollingGeneration, options.signal)
        && !isAbortError(caught)
      ) {
        error.value = caught instanceof Error ? caught.message : '加载发现任务失败'
      }
      return false
    } finally {
      if (requestGeneration === listGeneration && !isBackground) loading.value = false
    }
  }

  async function loadDetail(id: string, options: DetailOptions = {}): Promise<boolean> {
    const requestGeneration = ++detailGeneration
    detailController?.abort()
    const controller = new AbortController()
    detailController = controller

    const cancelFromParent = () => controller.abort()
    options.signal?.addEventListener('abort', cancelFromParent, { once: true })
    if (options.signal?.aborted) controller.abort()

    if (!options.preserveSelection) selected.value = null
    error.value = null
    try {
      const response = await discoveryApi.get(id, controller.signal)
      if (response.id !== id) throw new Error('发现任务详情响应与所选任务不一致')
      if (
        requestGeneration !== detailGeneration
        || !pollingRequestIsCurrent(options.pollingGeneration, controller.signal)
      ) return false
      if (options.preserveSelection && selected.value?.id !== id) return false

      selected.value = response
      return true
    } catch (caught) {
      if (
        requestGeneration === detailGeneration
        && pollingRequestIsCurrent(options.pollingGeneration, controller.signal)
        && !isAbortError(caught)
      ) {
        error.value = caught instanceof Error ? caught.message : '加载任务详情失败'
      }
      return false
    } finally {
      options.signal?.removeEventListener('abort', cancelFromParent)
      if (detailController === controller) detailController = null
    }
  }

  async function refreshSnapshot(options: LoadOptions = {}): Promise<boolean> {
    const selectedId = selected.value?.id
    const loaded = await loadRuns(options)
    if (!loaded) return false
    if (
      selectedId
      && selected.value?.id === selectedId
      && pollingRequestIsCurrent(options.pollingGeneration, options.signal)
    ) {
      await loadDetail(selectedId, {
        preserveSelection: true,
        signal: options.signal,
        pollingGeneration: options.pollingGeneration,
      })
    }
    return true
  }

  async function load(): Promise<void> {
    await loadRuns()
  }

  function cancelPolling(): number {
    pollingGeneration += 1
    pollingController?.abort()
    pollingController = null
    autoRefreshing.value = false
    return pollingGeneration
  }

  function stopAutoRefresh(): void {
    cancelPolling()
  }

  async function runAutoRefresh(
    generation: number,
    controller: AbortController,
    initialLoad: boolean,
  ): Promise<void> {
    if (initialLoad) {
      await refreshSnapshot({ signal: controller.signal, pollingGeneration: generation })
    }
    if (
      generation !== pollingGeneration
      || controller.signal.aborted
      || activeRunCount.value === 0
    ) return

    autoRefreshing.value = true
    try {
      while (
        generation === pollingGeneration
        && !controller.signal.aborted
        && activeRunCount.value > 0
      ) {
        await waitForPoll(DISCOVERY_POLL_INTERVAL_MS, controller.signal)
        await refreshSnapshot({
          background: true,
          signal: controller.signal,
          pollingGeneration: generation,
        })
      }
    } catch (caught) {
      if (
        generation === pollingGeneration
        && !isAbortError(caught)
      ) {
        error.value = caught instanceof Error ? caught.message : '自动刷新发现任务失败'
      }
    } finally {
      if (generation === pollingGeneration) autoRefreshing.value = false
    }
  }

  async function startAutoRefresh(initialLoad = true): Promise<void> {
    const previousTask = pollingTask
    const generation = cancelPolling()
    if (previousTask) await previousTask
    if (generation !== pollingGeneration) return

    const controller = new AbortController()
    pollingController = controller
    const task = runAutoRefresh(generation, controller, initialLoad)
    pollingTask = task
    try {
      await task
    } finally {
      if (pollingTask === task) pollingTask = null
      if (generation === pollingGeneration) {
        pollingController = null
        autoRefreshing.value = false
      }
    }
  }

  async function refresh(): Promise<void> {
    const previousTask = pollingTask
    const generation = cancelPolling()
    notice.value = null
    if (previousTask) await previousTask
    if (generation !== pollingGeneration) return

    await refreshSnapshot({ pollingGeneration: generation })
    if (generation === pollingGeneration && activeRunCount.value > 0) {
      void startAutoRefresh(false)
    }
  }

  async function selectRun(id: string): Promise<void> {
    await loadDetail(id)
  }

  async function create(): Promise<void> {
    if (creating.value) return
    const previousTask = pollingTask
    const generation = cancelPolling()
    if (previousTask) await previousTask
    if (generation !== pollingGeneration) return

    creating.value = true
    error.value = null
    notice.value = null
    try {
      const run = await discoveryApi.create()
      if (generation !== pollingGeneration) return
      await loadRuns({ pollingGeneration: generation })
      if (generation !== pollingGeneration) return
      await loadDetail(run.id, { pollingGeneration: generation })
      if (generation !== pollingGeneration) return
      notice.value = run.deduplicated
        ? '已有发现任务正在运行；页面将自动刷新 Worker 的最新结果'
        : '发现任务已创建；页面将自动刷新最新结果'
      if (activeRunCount.value > 0) void startAutoRefresh(false)
    } catch (caught) {
      if (generation === pollingGeneration) {
        error.value = caught instanceof Error ? caught.message : '创建发现任务失败'
      }
    } finally {
      creating.value = false
    }
  }

  return {
    runs,
    selected,
    total,
    page,
    pageSize,
    loading,
    creating,
    autoRefreshing,
    activeRunCount,
    retryWaitingCount,
    error,
    notice,
    load,
    refresh,
    selectRun,
    create,
    startAutoRefresh,
    stopAutoRefresh,
  }
})
