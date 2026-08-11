import { defineStore } from 'pinia'
import { ref } from 'vue'

import { mediaApi, ptSiteApi, torrentApi } from '../api/client'
import type {
  MediaItem,
  PtSiteCatalog,
  PtSiteCatalogItem,
  TorrentCandidateResult,
  TorrentSearchCreateRequest,
  TorrentSearchRun,
} from '../types'

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
  const working = ref(false)
  const catalogError = ref<string | null>(null)
  const error = ref<string | null>(null)
  const selectionError = ref<string | null>(null)
  const actionError = ref<string | null>(null)
  const notice = ref<string | null>(null)
  let activeMediaId: string | null = null
  let catalogGeneration = 0
  let generation = 0
  let selectionGeneration = 0
  let actionGeneration = 0
  let selectionController: AbortController | null = null

  function cancelSelection(): void {
    selectionGeneration += 1
    selectionController?.abort()
    selectionController = null
    selecting.value = false
  }

  function cancelAction(): void {
    actionGeneration += 1
    working.value = false
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
      if (runResponse[0]) await selectRun(runResponse[0].id)
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

  async function create(
    mediaId: string,
    payload: TorrentSearchCreateRequest,
  ): Promise<boolean> {
    const current = ++actionGeneration
    const expectedGeneration = generation
    const site = sites.value.find((item) => item.site_id === payload.site_id)
    const mediaTypeSupported = Boolean(media.value && site?.media_types.includes(media.value.media_type))
    working.value = true
    actionError.value = null
    notice.value = null
    try {
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
        ? `${site.display_name} 已有只读搜索正在运行；可刷新查看 Worker 的最新结果`
        : `${site.display_name} 只读搜索已创建；任务异步执行，可刷新查看最新结果`
      await selectRun(result.id)
      return (
        current === actionGeneration &&
        expectedGeneration === generation &&
        activeMediaId === mediaId
      )
    } catch (caught) {
      if (current === actionGeneration && expectedGeneration === generation) {
        actionError.value = caught instanceof Error ? caught.message : '创建 PT 搜索失败'
      }
      return false
    } finally {
      if (current === actionGeneration) working.value = false
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
    working,
    catalogError,
    error,
    selectionError,
    actionError,
    notice,
    loadCatalog,
    load,
    selectRun,
    cancelSelection,
    create,
  }
})
