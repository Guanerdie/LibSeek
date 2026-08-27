import { defineStore } from 'pinia'

import { dailyApi, ApiError } from '../api/client'
import type {
  DailyDownload,
  DailyMedia,
  DailyMediaDetail,
  DailyMediaFilterOptions,
  DailyMediaQuery,
  DailySearch,
} from '../types'

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
  }),
  actions: {
    async syncMedia(): Promise<void> {
      if (this.mediaSyncing) return

      this.mediaSyncing = true
      this.mediaError = null
      try {
        await dailyApi.syncMedia()
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
    async startSearch(mediaId: string, siteIds: string[]): Promise<void> {
      this.resourceError = null
      try {
        this.search = await dailyApi.createSearch(mediaId, siteIds)
      } catch (error) {
        this.resourceError = error instanceof ApiError ? error.message : '无法开始搜索'
      }
    },
    async loadSearch(searchId: string): Promise<void> {
      this.resourceError = null
      try {
        this.search = await dailyApi.search(searchId)
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
