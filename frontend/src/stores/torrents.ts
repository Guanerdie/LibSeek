import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { approvalApi, mediaApi, ptSiteApi, torrentApi } from '../api/client'
import type {
  ConfirmDownloadResponse,
  DownloadLaunchMode,
  MediaItem,
  PtSiteCatalog,
  PtSiteCatalogItem,
  TorrentCandidateResult,
  TorrentSearchCreateRequest,
  TorrentSearchRun,
} from '../types'
import { hasConfirmedIdentityEvidence } from '../utils/identity'
import { isAbortError, waitForPoll } from '../utils/polling'

const SEARCH_POLL_INTERVAL_MS = 1_500
const SEARCH_POLL_MAX_ATTEMPTS = 80
const SEARCH_TERMINAL_STATUSES = new Set<TorrentSearchRun['status']>([
  'TORRENT_REVIEW',
  'NO_CANDIDATE',
  'SEARCH_FAILED',
])

function randomIdempotencyKey(): string {
  const bytes = new Uint8Array(24)
  globalThis.crypto.getRandomValues(bytes)
  return `unin-${Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')}`
}

export const useTorrentStore = defineStore('torrents', () => {
  const media = ref<MediaItem | null>(null)
  const sites = ref<PtSiteCatalogItem[]>([])
  const defaultSiteId = ref<string | null>(null)
  const selectedSiteId = ref('')
  const runs = ref<TorrentSearchRun[]>([])
  const selectedRunId = ref<string | null>(null)
  const selectedRun = ref<TorrentSearchRun | null>(null)
  const candidates = ref<TorrentCandidateResult[]>([])
  const catalogLoading = ref(false)
  const loading = ref(false)
  const selecting = ref(false)
  const autoRefreshing = ref(false)
  const working = ref(false)
  const catalogError = ref<string | null>(null)
  const error = ref<string | null>(null)
  const selectionError = ref<string | null>(null)
  const actionError = ref<string | null>(null)
  const notice = ref<string | null>(null)
  const downloadResult = ref<ConfirmDownloadResponse | null>(null)
  const downloadingCandidateId = ref<string | null>(null)
  let activeMediaId: string | null = null
  let catalogGeneration = 0
  let generation = 0
  let selectionGeneration = 0
  let pollingGeneration = 0
  let actionGeneration = 0
  let selectionController: AbortController | null = null
  let pollingController: AbortController | null = null
  let actionController: AbortController | null = null
  const downloadIdempotencyKeys = new Map<string, string>()
  const identityGatePassed = computed(() =>
    hasConfirmedIdentityEvidence(media.value, runs.value),
  )

  function cancelSelection(): void {
    selectionGeneration += 1
    selectionController?.abort()
    selectionController = null
    selecting.value = false
  }

  function cancelRunPolling(): void {
    pollingGeneration += 1
    pollingController?.abort()
    pollingController = null
    autoRefreshing.value = false
  }

  function cancelAction(): void {
    actionGeneration += 1
    actionController?.abort()
    actionController = null
    working.value = false
    actionError.value = null
  }

  function resetDownloadConfirmation(): void {
    downloadResult.value = null
    downloadingCandidateId.value = null
    actionError.value = null
  }

  function validateCatalog(result: PtSiteCatalog): void {
    const siteIds = new Set(result.sites.map((site) => site.site_id))
    if (siteIds.size !== result.sites.length) {
      throw new Error('PT 站点目录响应包含重复站点，请刷新后重试')
    }
    if (result.default_site_id !== null && !siteIds.has(result.default_site_id)) {
      throw new Error('PT 站点目录默认项响应不一致，请刷新后重试')
    }
  }

  function chooseSite(result: PtSiteCatalog, previousSiteId: string): string {
    const previous = result.sites.find((site) => site.site_id === previousSiteId)
    if (previous?.available_for_search) return previous.site_id
    const defaultSite = result.sites.find((site) => site.site_id === result.default_site_id)
    if (defaultSite?.available_for_search) return defaultSite.site_id
    const available = result.sites.find((site) => site.available_for_search)
    return available?.site_id ?? defaultSite?.site_id ?? result.sites[0]?.site_id ?? ''
  }

  async function loadCatalog(): Promise<void> {
    const current = ++catalogGeneration
    const previousSiteId = selectedSiteId.value
    sites.value = []
    defaultSiteId.value = null
    selectedSiteId.value = ''
    catalogLoading.value = true
    catalogError.value = null
    try {
      const result = await ptSiteApi.catalog()
      if (current !== catalogGeneration) return
      validateCatalog(result)
      sites.value = result.sites
      defaultSiteId.value = result.default_site_id
      selectedSiteId.value = chooseSite(result, previousSiteId)
    } catch (caught) {
      if (current !== catalogGeneration) return
      sites.value = []
      defaultSiteId.value = null
      selectedSiteId.value = ''
      catalogError.value = caught instanceof Error ? caught.message : '加载 PT 站点目录失败'
    } finally {
      if (current === catalogGeneration) catalogLoading.value = false
    }
  }

  async function load(mediaId: string): Promise<void> {
    const current = ++generation
    activeMediaId = mediaId
    cancelRunPolling()
    cancelSelection()
    cancelAction()
    media.value = null
    runs.value = []
    selectedRunId.value = null
    selectedRun.value = null
    candidates.value = []
    loading.value = true
    error.value = null
    selectionError.value = null
    notice.value = null
    downloadResult.value = null
    downloadingCandidateId.value = null
    try {
      const [mediaResponse, runResponse] = await Promise.all([
        mediaApi.get(mediaId),
        torrentApi.list(mediaId),
      ])
      if (current !== generation) return
      const runIds = new Set(runResponse.map((run) => run.id))
      if (
        mediaResponse.id !== mediaId ||
        runIds.size !== runResponse.length ||
        runResponse.some((run) => run.media_id !== mediaId)
      ) {
        throw new Error('PT 搜索列表响应身份不一致，请刷新后重试')
      }
      media.value = mediaResponse
      runs.value = runResponse
      if (runResponse[0]) {
        await selectRun(runResponse[0].id)
        const loadedRun = selectedRun.value as TorrentSearchRun | null
        if (
          current === generation &&
          loadedRun &&
          !SEARCH_TERMINAL_STATUSES.has(loadedRun.status)
        ) {
          startRunPolling(mediaId, loadedRun.id, current)
        }
      }
    } catch (caught) {
      if (current !== generation) return
      media.value = null
      runs.value = []
      selectedRunId.value = null
      selectedRun.value = null
      candidates.value = []
      error.value = caught instanceof Error ? caught.message : '加载 PT 候选失败'
    } finally {
      if (current === generation) loading.value = false
    }
  }

  async function selectRun(searchId: string): Promise<void> {
    cancelRunPolling()
    const expectedMediaId = activeMediaId
    const listedRun = runs.value.find((run) => run.id === searchId)
    if (!expectedMediaId || !listedRun) {
      cancelSelection()
      selectedRunId.value = null
      selectedRun.value = null
      candidates.value = []
      selectionError.value = 'PT 搜索运行不存在或已变更，请刷新后重试'
      return
    }

    const current = ++selectionGeneration
    const expectedGeneration = generation
    selectionController?.abort()
    const controller = new AbortController()
    selectionController = controller
    selectedRunId.value = searchId
    selectedRun.value = null
    candidates.value = []
    selectionError.value = null
    selecting.value = true
    try {
      const [run, results] = await Promise.all([
        torrentApi.get(searchId, controller.signal),
        torrentApi.candidates(searchId, controller.signal),
      ])
      if (current !== selectionGeneration || expectedGeneration !== generation) return
      if (
        run.id !== searchId ||
        run.media_id !== expectedMediaId ||
        run.site_id !== listedRun.site_id ||
        results.some(
          (result) =>
            result.search_run_id !== searchId || result.candidate.site_id !== run.site_id,
        )
      ) {
        throw new Error('PT 搜索运行或候选响应身份不一致，请刷新后重试')
      }
      selectedRun.value = run
      runs.value = runs.value.map((item) => (item.id === run.id ? run : item))
      candidates.value = results
    } catch (caught) {
      if (
        current !== selectionGeneration ||
        expectedGeneration !== generation ||
        (caught instanceof Error && caught.name === 'AbortError')
      ) {
        return
      }
      selectedRun.value = null
      candidates.value = []
      selectionError.value = caught instanceof Error ? caught.message : '加载搜索结果失败'
    } finally {
      if (current === selectionGeneration && expectedGeneration === generation) {
        selecting.value = false
        selectionController = null
      }
    }
  }

  function startRunPolling(
    mediaId: string,
    searchId: string,
    expectedGeneration: number,
  ): void {
    cancelRunPolling()
    const current = ++pollingGeneration
    const controller = new AbortController()
    pollingController = controller
    autoRefreshing.value = true

    void (async () => {
      try {
        for (let attempt = 1; attempt < SEARCH_POLL_MAX_ATTEMPTS; attempt += 1) {
          await waitForPoll(SEARCH_POLL_INTERVAL_MS, controller.signal)
          const listedRun = runs.value.find((run) => run.id === searchId)
          if (
            current !== pollingGeneration ||
            expectedGeneration !== generation ||
            activeMediaId !== mediaId ||
            selectedRunId.value !== searchId ||
            !listedRun
          ) return

          const [run, results] = await Promise.all([
            torrentApi.get(searchId, controller.signal),
            torrentApi.candidates(searchId, controller.signal),
          ])
          if (
            run.id !== searchId ||
            run.media_id !== mediaId ||
            run.site_id !== listedRun.site_id ||
            results.some(
              (result) =>
                result.search_run_id !== searchId || result.candidate.site_id !== run.site_id,
            )
          ) {
            throw new Error('PT 搜索运行或候选响应身份不一致，请刷新后重试')
          }
          if (
            current !== pollingGeneration ||
            expectedGeneration !== generation ||
            selectedRunId.value !== searchId
          ) return

          selectedRun.value = run
          runs.value = runs.value.map((item) => (item.id === run.id ? run : item))
          candidates.value = results
          if (!SEARCH_TERMINAL_STATUSES.has(run.status)) continue

          if (run.status === 'SEARCH_FAILED') {
            const detail = run.error_message?.trim() || 'PT 搜索失败'
            selectionError.value = run.error_code ? `${run.error_code} · ${detail}` : detail
          } else {
            notice.value = run.status === 'NO_CANDIDATE'
              ? 'PT 搜索完成，未找到候选。'
              : `PT 搜索完成，找到 ${run.candidate_count} 个候选。`
          }
          return
        }
        notice.value = 'PT 搜索仍在后台运行，可稍后刷新结果。'
      } catch (caught) {
        if (current === pollingGeneration && !isAbortError(caught)) {
          selectionError.value = caught instanceof Error ? caught.message : '刷新 PT 搜索结果失败'
        }
      } finally {
        if (current === pollingGeneration) {
          pollingController = null
          autoRefreshing.value = false
        }
      }
    })()
  }

  async function create(
    mediaId: string,
    payload: TorrentSearchCreateRequest,
  ): Promise<boolean> {
    cancelRunPolling()
    cancelAction()
    const current = ++actionGeneration
    const expectedGeneration = generation
    const controller = new AbortController()
    actionController = controller
    const site = sites.value.find((item) => item.site_id === payload.site_id)
    const mediaTypeSupported = Boolean(media.value && site?.media_types.includes(media.value.media_type))
    working.value = true
    actionError.value = null
    notice.value = null
    try {
      if (!identityGatePassed.value) {
        throw new Error('必须先确认影视身份，才能创建 PT 搜索')
      }
      if (
        activeMediaId !== mediaId ||
        media.value?.id !== mediaId ||
        selectedSiteId.value !== payload.site_id ||
        !site?.available_for_search ||
        !mediaTypeSupported
      ) {
        throw new Error('所选 PT 站点当前不可用于该媒体搜索，请刷新站点目录')
      }
      const result = await torrentApi.create(mediaId, payload)
      if (
        current !== actionGeneration ||
        expectedGeneration !== generation ||
        activeMediaId !== mediaId
      ) {
        return false
      }
      if (result.media_id !== mediaId || result.site_id !== payload.site_id) {
        throw new Error('新建 PT 搜索响应身份不一致，请刷新后重试')
      }
      runs.value = [result, ...runs.value.filter((run) => run.id !== result.id)]
      notice.value = result.deduplicated
        ? `${site.display_name} 已有搜索正在运行，正在等待结果。`
        : `${site.display_name} 搜索已创建，正在等待结果。`

      for (let attempt = 0; attempt < SEARCH_POLL_MAX_ATTEMPTS; attempt += 1) {
        if (attempt > 0) await waitForPoll(SEARCH_POLL_INTERVAL_MS, controller.signal)
        await selectRun(result.id)
        if (
          current !== actionGeneration ||
          expectedGeneration !== generation ||
          activeMediaId !== mediaId
        ) {
          return false
        }
        if (selectionError.value || !selectedRun.value) {
          throw new Error(selectionError.value || 'PT 搜索状态暂时不可用')
        }
        if (SEARCH_TERMINAL_STATUSES.has(selectedRun.value.status)) {
          if (selectedRun.value.status === 'SEARCH_FAILED') {
            const detail = selectedRun.value.error_message?.trim() || 'PT 搜索失败'
            actionError.value = selectedRun.value.error_code
              ? `${selectedRun.value.error_code} · ${detail}`
              : detail
            notice.value = null
            return false
          }
          notice.value = selectedRun.value.status === 'NO_CANDIDATE'
            ? `${site.display_name} 搜索完成，未找到候选。`
            : `${site.display_name} 搜索完成，找到 ${selectedRun.value.candidate_count} 个候选。`
          return true
        }
        notice.value = `${site.display_name} 搜索仍在进行（${attempt + 1}/${SEARCH_POLL_MAX_ATTEMPTS}）…`
      }

      notice.value = `${site.display_name} 搜索等待超时；任务可能仍在后台运行，可稍后刷新结果。`
      return (
        current === actionGeneration &&
        expectedGeneration === generation &&
        activeMediaId === mediaId
      )
    } catch (caught) {
      if (
        current === actionGeneration &&
        expectedGeneration === generation &&
        !isAbortError(caught)
      ) {
        actionError.value = caught instanceof Error ? caught.message : '创建 PT 搜索失败'
      }
      return false
    } finally {
      if (current === actionGeneration) {
        working.value = false
        actionController = null
      }
    }
  }

  async function confirmDownload(
    candidateId: string,
    launchMode: DownloadLaunchMode = 'START_IMMEDIATELY',
  ): Promise<ConfirmDownloadResponse | null> {
    cancelAction()
    const current = ++actionGeneration
    const expectedGeneration = generation
    const expectedMediaId = activeMediaId
    const expectedRunId = selectedRun.value?.id
    const candidate = candidates.value.find((item) => item.id === candidateId)
    const controller = new AbortController()
    actionController = controller
    working.value = true
    actionError.value = null
    downloadResult.value = null
    downloadingCandidateId.value = candidateId

    const keyScope = `${candidateId}:${launchMode}`
    const idempotencyKey = downloadIdempotencyKeys.get(keyScope) ?? randomIdempotencyKey()
    downloadIdempotencyKeys.set(keyScope, idempotencyKey)

    try {
      if (
        !expectedMediaId ||
        !expectedRunId ||
        !candidate ||
        candidate.search_run_id !== expectedRunId
      ) {
        throw new Error('所选候选已变更，请刷新后重新选择')
      }
      const result = await approvalApi.confirmDownload(
        candidateId,
        launchMode,
        idempotencyKey,
        controller.signal,
      )
      if (
        current !== actionGeneration ||
        expectedGeneration !== generation ||
        activeMediaId !== expectedMediaId ||
        selectedRun.value?.id !== expectedRunId
      ) {
        return null
      }
      if (
        result.approval.media_item_id !== expectedMediaId ||
        result.approval.torrent_candidate_id !== candidateId ||
        result.approval.preflight_result?.policy_fingerprint !== result.preflight.policy_fingerprint
      ) {
        throw new Error('下载确认响应与当前候选不一致，请刷新后重试')
      }
      if (
        result.outcome === 'EXECUTION_CREATED' ||
        result.outcome === 'EXECUTION_REPLAYED'
      ) {
        if (!result.execution || result.execution.approval_id !== result.approval.id) {
          throw new Error('下载执行响应绑定无效，请前往下载页面核对')
        }
        notice.value = '已通过预检并加入下载队列。'
      } else if (result.outcome === 'PREFLIGHT_BLOCKED') {
        if (result.execution !== null) {
          throw new Error('预检阻断响应包含异常执行记录，请刷新后重试')
        }
        downloadIdempotencyKeys.delete(keyScope)
        notice.value = null
      } else {
        throw new Error('下载确认返回了未知状态，请刷新后重试')
      }
      downloadResult.value = result
      return result
    } catch (caught) {
      if (
        current === actionGeneration &&
        expectedGeneration === generation &&
        !isAbortError(caught)
      ) {
        actionError.value = caught instanceof Error ? caught.message : '确认下载失败'
      }
      return null
    } finally {
      if (current === actionGeneration) {
        working.value = false
        actionController = null
      }
    }
  }

  return {
    media,
    sites,
    defaultSiteId,
    selectedSiteId,
    runs,
    selectedRunId,
    selectedRun,
    candidates,
    catalogLoading,
    loading,
    selecting,
    autoRefreshing,
    working,
    catalogError,
    error,
    selectionError,
    actionError,
    notice,
    downloadResult,
    downloadingCandidateId,
    identityGatePassed,
    loadCatalog,
    load,
    selectRun,
    cancelSelection,
    cancelRunPolling,
    cancelAction,
    resetDownloadConfirmation,
    create,
    confirmDownload,
  }
})
