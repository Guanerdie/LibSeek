import { defineStore } from 'pinia'

import { dailyApi, ApiError } from '../api/client'
import type { DailyDownload, DailyMedia, DailyMediaDetail, DailySearch } from '../types'

interface DailyState {
  media: DailyMedia[]
  selectedMedia: DailyMediaDetail | null
  search: DailySearch | null
  downloads: DailyDownload[]
  loading: boolean
  error: string | null
}

export const useDailyStore = defineStore('daily', {
  state: (): DailyState => ({
    media: [],
    selectedMedia: null,
    search: null,
    downloads: [],
    loading: false,
    error: null,
  }),
  actions: {
    async syncMedia(): Promise<void> {
      this.loading = true
      this.error = null
      try {
        await dailyApi.syncMedia()
        this.media = (await dailyApi.media()).items
      } catch (error) {
        this.error = error instanceof ApiError ? error.message : '无法同步缺失影视'
      } finally {
        this.loading = false
      }
    },
    async loadMedia(): Promise<void> {
      this.loading = true
      this.error = null
      try {
        this.media = (await dailyApi.media()).items
      } catch (error) {
        this.media = []
        this.error = error instanceof ApiError ? error.message : '无法读取缺失影视'
      } finally {
        this.loading = false
      }
    },
    async loadMediaDetail(mediaId: string): Promise<void> {
      this.loading = true
      this.error = null
      this.selectedMedia = null
      try {
        this.selectedMedia = await dailyApi.mediaDetail(mediaId)
      } catch (error) {
        this.error = error instanceof ApiError ? error.message : '无法读取影视详情'
      } finally {
        this.loading = false
      }
    },
    async identify(mediaId: string, tmdbId?: number): Promise<boolean> {
      this.error = null
      try {
        await dailyApi.identify(mediaId, tmdbId)
        this.selectedMedia = await dailyApi.mediaDetail(mediaId)
        return true
      } catch (error) {
        this.error = error instanceof ApiError ? error.message : '无法确认 TMDB 影视信息'
        return false
      }
    },
    async startSearch(mediaId: string, siteIds: string[]): Promise<void> {
      this.error = null
      try {
        this.search = await dailyApi.createSearch(mediaId, siteIds)
      } catch (error) {
        this.error = error instanceof ApiError ? error.message : '无法开始搜索'
      }
    },
    async loadSearch(searchId: string): Promise<void> {
      this.error = null
      try {
        this.search = await dailyApi.search(searchId)
      } catch (error) {
        this.error = error instanceof ApiError ? error.message : '无法读取搜索结果'
      }
    },
    async download(candidateId: string, confirmWarnings = false): Promise<boolean> {
      this.error = null
      try {
        await dailyApi.downloadCandidate(candidateId, confirmWarnings)
        return true
      } catch (error) {
        this.error = error instanceof ApiError ? error.message : '无法创建下载'
        return false
      }
    },
    async loadDownloads(): Promise<void> {
      this.loading = true
      this.error = null
      try {
        this.downloads = (await dailyApi.downloads()).items
      } catch (error) {
        this.downloads = []
        this.error = error instanceof ApiError ? error.message : '无法读取下载任务'
      } finally {
        this.loading = false
      }
    },
    async refreshDownloads(): Promise<void> {
      this.loading = true
      this.error = null
      try {
        await dailyApi.syncDownloads()
        this.downloads = (await dailyApi.downloads()).items
      } catch (error) {
        this.error = error instanceof ApiError ? error.message : '无法同步下载状态'
      } finally {
        this.loading = false
      }
    },
  },
})
