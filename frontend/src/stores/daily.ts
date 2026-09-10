import { defineStore } from 'pinia'

import { dailyApi, ApiError } from '../api/client'
import type {
  DailyDownload,
  DailyMedia,
  DailyMediaDetail,
  DailyMediaFilterOptions,
  DailyMediaQuery,
  DailySearch,
  LibrarySyncStatus,
  QuickFillResult,
} from '../types'

// Searches and NextFind syncs run on the server in the background; the page
// follows them by polling instead of holding one request open for minutes.
const SEARCH_POLL_INTERVAL_MS = 1000
const SEARCH_POLL_LIMIT_MS = 5 * 60_000
const SYNC_POLL_INTERVAL_MS = 1500
const SYNC_POLL_LIMIT_MS = 15 * 60_000

function isSearchActive(search: DailySearch | null): boolean {
  return search?.state === 'PENDING' || search?.state === 'RUNNING'
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, ms))
}

/** Poll a background NextFind sync until it ends; null if it outlasts the wait. */
async function followLibrarySync(initial: LibrarySyncStatus): Promise<LibrarySyncStatus | null> {
  let status = initial
  const deadline = Date.now() + SYNC_POLL_LIMIT_MS
  while (status.state === 'RUNNING') {
    if (Date.now() > deadline) return null
    await sleep(SYNC_POLL_INTERVAL_MS)
    status = await dailyApi.syncStatus()
  }
  return status
}

interface DailyState {
  media: DailyMedia[]
  mediaTotal: number
  mediaPage: number
  mediaPageSize: number
  mediaFilterOptions: DailyMediaFilterOptions
  mediaQuery: DailyMediaQuery
  selectedMedia: DailyMediaDetail | null
  search: DailySearch | null
  downloads: DailyDownload[]
  downloadsTotal: number
  downloadsPage: number
  downloadsPageSize: number
  mediaListLoading: boolean
  mediaSyncing: boolean
  mediaDetailLoading: boolean
  downloadsLoading: boolean
  downloadsSyncing: boolean
  mediaError: string | null
  resourceError: string | null
  downloadsError: string | null
  mediaRequestSequence: number
  mediaListRequests: number
  searchPollGeneration: number
}

export const useDailyStore = defineStore('daily', {
  state: (): DailyState => ({
    media: [],
    mediaTotal: 0,
    mediaPage: 1,
    mediaPageSize: 30,
    mediaFilterOptions: { media_types: [], regions: [], states: [], years: [] },
    mediaQuery: {},
    selectedMedia: null,
    search: null,
    downloads: [],
    downloadsTotal: 0,
    downloadsPage: 1,
    downloadsPageSize: 30,
    mediaListLoading: false,
    mediaSyncing: false,
    mediaDetailLoading: false,
    downloadsLoading: false,
    downloadsSyncing: false,
    mediaError: null,
    resourceError: null,
    downloadsError: null,
    mediaRequestSequence: 0,
    mediaListRequests: 0,
    searchPollGeneration: 0,
  }),
  actions: {
    async syncMedia(): Promise<void> {
      if (this.mediaSyncing) return

      this.mediaSyncing = true
      this.mediaError = null
      try {
        let status = await dailyApi.syncMedia()
        if (status.state === 'RUNNING') {
          const finished = await followLibrarySync(status)
          if (finished === null) {
            this.mediaError = '同步仍在后台进行，请稍后刷新列表'
            return
          }
          status = finished
        }
        if (status.state === 'FAILED') {
          this.mediaError = status.error_message || '无法同步缺失影视'
          return
        }
        const requestSequence = ++this.mediaRequestSequence
        const response = await dailyApi.media(this.mediaQuery)
        if (requestSequence === this.mediaRequestSequence) {
          this.media = response.items
          this.mediaTotal = response.total
          this.mediaPage = response.page
          this.mediaPageSize = response.page_size
          this.mediaFilterOptions = response.filter_options
        }
      } catch (error) {
        this.mediaError = error instanceof ApiError ? error.message : '无法同步缺失影视'
      } finally {
        this.mediaSyncing = false
      }
    },
    async loadMedia(query?: DailyMediaQuery): Promise<void> {
      if (query) this.mediaQuery = { ...query }
      const requestSequence = ++this.mediaRequestSequence
      this.mediaListRequests += 1
      this.mediaListLoading = true
      this.mediaError = null
      try {
        const response = await dailyApi.media(this.mediaQuery)
        if (requestSequence !== this.mediaRequestSequence) return
        this.media = response.items
        this.mediaTotal = response.total
        this.mediaPage = response.page
        this.mediaPageSize = response.page_size
        this.mediaFilterOptions = response.filter_options
      } catch (error) {
        if (requestSequence !== this.mediaRequestSequence) return
        this.media = []
        this.mediaTotal = 0
        this.mediaError = error instanceof ApiError ? error.message : '无法读取缺失影视'
      } finally {
        this.mediaListRequests -= 1
        this.mediaListLoading = this.mediaListRequests > 0
      }
    },
    async loadMediaDetail(mediaId: string): Promise<void> {
      this.mediaDetailLoading = true
      this.resourceError = null
      this.selectedMedia = null
      this.search = null
      this.stopFollowingSearch()
      try {
        const selectedMedia = await dailyApi.mediaDetail(mediaId)
        this.selectedMedia = selectedMedia
        this.search = selectedMedia.latest_search
      } catch (error) {
        this.resourceError = error instanceof ApiError ? error.message : '无法读取影视详情'
      } finally {
        this.mediaDetailLoading = false
      }
    },
    async setSubscription(mediaId: string, subscribed: boolean): Promise<boolean> {
      this.resourceError = null
      try {
        this.selectedMedia = await dailyApi.setSubscription(mediaId, subscribed)
        return true
      } catch (error) {
        this.resourceError = error instanceof ApiError ? error.message : '无法更新订阅'
        return false
      }
    },
    async setSubscriptions(mediaIds: string[], subscribed: boolean): Promise<number | null> {
      this.mediaError = null
      try {
        const result = await dailyApi.setSubscriptions(mediaIds, subscribed)
        return result.changed
      } catch (error) {
        this.mediaError = error instanceof ApiError ? error.message : '无法批量更新追更清单'
        return null
      }
    },
    async setMinimumScore(mediaId: string, minimumScore: number | null): Promise<boolean> {
      this.resourceError = null
      try {
        this.selectedMedia = await dailyApi.setMinimumScore(mediaId, minimumScore)
        return true
      } catch (error) {
        this.resourceError = error instanceof ApiError ? error.message : '无法设置单片门槛'
        return false
      }
    },
    async identify(mediaId: string, tmdbId?: number): Promise<boolean> {
      this.resourceError = null
      try {
        await dailyApi.identify(mediaId, tmdbId)
        this.selectedMedia = await dailyApi.mediaDetail(mediaId)
        return true
      } catch (error) {
        this.resourceError = error instanceof ApiError ? error.message : '无法确认 TMDB 影视信息'
        return false
      }
    },
    /** Omitting ``siteIds`` searches the sites the automation policy uses. */
    async startSearch(mediaId: string, siteIds?: string[], force = false): Promise<void> {
      this.resourceError = null
      try {
        this.search = await dailyApi.createSearch(mediaId, siteIds, force)
        await this.followSearch()
      } catch (error) {
        this.resourceError = error instanceof ApiError ? error.message : '无法开始搜索'
      }
    },
    /**
     * Poll the current search until the site has answered.
     *
     * Resolves to the finished search, or null once a newer search, another
     * media item or leaving the page took over -- or the wait ran out.
     */
    async followSearch(): Promise<DailySearch | null> {
      const generation = ++this.searchPollGeneration
      const deadline = Date.now() + SEARCH_POLL_LIMIT_MS
      while (this.search && isSearchActive(this.search)) {
        if (Date.now() > deadline) {
          this.resourceError = '搜索仍在后台进行，请稍后刷新页面查看结果'
          return null
        }
        await sleep(SEARCH_POLL_INTERVAL_MS)
        if (generation !== this.searchPollGeneration) return null
        const next = await dailyApi.search(this.search.id)
        if (generation !== this.searchPollGeneration) return null
        this.search = next
      }
      return this.search
    },
    stopFollowingSearch(): void {
      this.searchPollGeneration += 1
    },
    async quickFill(mediaId: string, force = false): Promise<QuickFillResult | null> {
      this.resourceError = null
      try {
        // Search first and follow it like any other search; quick fill then
        // reuses that fresh result instead of holding one request open across
        // the whole PT search.
        this.search = await dailyApi.createSearch(mediaId, undefined, force)
        const finished = await this.followSearch()
        if (!finished) return null
        if (finished.state === 'FAILED') {
          this.resourceError = finished.error_message || '搜索失败'
          return null
        }
        const result = await dailyApi.quickFill(mediaId, false)
        this.search = result.search
        this.selectedMedia = await dailyApi.mediaDetail(mediaId)
        return result
      } catch (error) {
        this.resourceError = error instanceof ApiError ? error.message : '一键补片失败'
        return null
      }
    },
    async loadSearch(searchId: string): Promise<void> {
      this.resourceError = null
      try {
        this.search = await dailyApi.search(searchId)
        await this.followSearch()
      } catch (error) {
        this.resourceError = error instanceof ApiError ? error.message : '无法读取搜索结果'
      }
    },
    async download(candidateId: string, confirmWarnings = false): Promise<boolean> {
      this.resourceError = null
      try {
        await dailyApi.downloadCandidate(candidateId, confirmWarnings)
        return true
      } catch (error) {
        this.resourceError = error instanceof ApiError ? error.message : '无法创建下载'
        return false
      }
    },
    async retryDownload(download: DailyDownload): Promise<boolean> {
      this.downloadsError = null
      try {
        const retried = await dailyApi.retryDownload(download.id)
        const index = this.downloads.findIndex((item) => item.id === download.id)
        if (index >= 0) this.downloads[index] = retried
        return true
      } catch (error) {
        this.downloadsError = error instanceof ApiError ? error.message : '无法重试下载'
        return false
      }
    },
    async loadDownloads(page?: number): Promise<void> {
      this.downloadsLoading = true
      this.downloadsError = null
      try {
        const requestedPage = page ?? this.downloadsPage
        const response = await dailyApi.downloads({
          page: requestedPage,
          pageSize: this.downloadsPageSize,
        })
        this.downloads = response.items
        this.downloadsTotal = response.total
        this.downloadsPage = response.page
        this.downloadsPageSize = response.page_size
      } catch (error) {
        this.downloads = []
        this.downloadsError = error instanceof ApiError ? error.message : '无法读取下载任务'
      } finally {
        this.downloadsLoading = false
      }
    },
    async refreshDownloads(page?: number): Promise<void> {
      this.downloadsSyncing = true
      this.downloadsError = null
      try {
        await dailyApi.syncDownloads()
        const requestedPage = page ?? this.downloadsPage
        const response = await dailyApi.downloads({
          page: requestedPage,
          pageSize: this.downloadsPageSize,
        })
        this.downloads = response.items
        this.downloadsTotal = response.total
        this.downloadsPage = response.page
        this.downloadsPageSize = response.page_size
      } catch (error) {
        this.downloadsError = error instanceof ApiError ? error.message : '无法同步下载状态'
      } finally {
        this.downloadsSyncing = false
      }
    },
  },
})
