import { defineStore } from 'pinia'
import { ref } from 'vue'

import { downloadJobApi } from '../api/client'
import type {
  DownloadJob,
  DownloadJobStatus,
  DownloadJobSummary,
  DownloadJobTimelineItem,
} from '../types'

export const useDownloadJobStore = defineStore('download-jobs', () => {
  const jobs = ref<DownloadJob[]>([])
  const selected = ref<DownloadJob | null>(null)
  const summary = ref<DownloadJobSummary | null>(null)
  const timeline = ref<DownloadJobTimelineItem[]>([])
  const page = ref(1)
  const pageSize = ref(50)
  const total = ref(0)
  const listLoading = ref(false)
  const detailLoading = ref(false)
  const listError = ref<string | null>(null)
  const detailError = ref<string | null>(null)
  let listGeneration = 0
  let detailGeneration = 0

  async function loadList(status?: DownloadJobStatus, requestedPage = 1): Promise<void> {
    const current = ++listGeneration
    listLoading.value = true
    listError.value = null
    jobs.value = []
    page.value = requestedPage
    total.value = 0
    try {
      const result = await downloadJobApi.list({
        page: requestedPage,
        pageSize: pageSize.value,
        status,
      })
      if (current !== listGeneration) return
      jobs.value = result.items
      page.value = result.page
      pageSize.value = result.page_size
      total.value = result.total
    } catch (caught) {
      if (current !== listGeneration) return
      jobs.value = []
      total.value = 0
      listError.value = caught instanceof Error ? caught.message : '加载下载任务列表失败'
    } finally {
      if (current === listGeneration) listLoading.value = false
    }
  }

  async function loadDetail(jobId: string): Promise<void> {
    const current = ++detailGeneration
    detailLoading.value = true
    detailError.value = null
    selected.value = null
    summary.value = null
    timeline.value = []
    try {
      const [job, jobSummary, jobTimeline] = await Promise.all([
        downloadJobApi.get(jobId),
        downloadJobApi.summary(jobId),
        downloadJobApi.timeline(jobId),
      ])
      if (current !== detailGeneration) return
      if (job.id !== jobId || jobSummary.job.id !== jobId || jobTimeline.job_id !== jobId) {
        throw new Error('下载任务详情响应不一致，请刷新后重试')
      }
      selected.value = job
      summary.value = jobSummary
      timeline.value = jobTimeline.items
    } catch (caught) {
      if (current !== detailGeneration) return
      selected.value = null
      summary.value = null
      timeline.value = []
      detailError.value = caught instanceof Error ? caught.message : '加载下载任务详情失败'
    } finally {
      if (current === detailGeneration) detailLoading.value = false
    }
  }

  return {
    jobs,
    selected,
    summary,
    timeline,
    page,
    pageSize,
    total,
    listLoading,
    detailLoading,
    listError,
    detailError,
    loadList,
    loadDetail,
  }
})
