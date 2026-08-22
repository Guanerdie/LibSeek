import { defineStore } from 'pinia'
import { ref } from 'vue'

import { downloadBatchApi } from '../api/client'
import type { DownloadBatch, DownloadBatchCreateRequest, DownloadBatchSummary } from '../types'

export const useDownloadBatchStore = defineStore('downloadBatches', () => {
  const batches = ref<DownloadBatchSummary[]>([])
  const current = ref<DownloadBatch | null>(null)
  const loading = ref(false)
  const working = ref(false)
  const error = ref<string | null>(null)

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    try { batches.value = (await downloadBatchApi.list()).items }
    catch (caught) { error.value = caught instanceof Error ? caught.message : '加载下载批次失败' }
    finally { loading.value = false }
  }

  async function loadOne(id: string): Promise<void> {
    loading.value = true
    error.value = null
    try { current.value = await downloadBatchApi.get(id) }
    catch (caught) { error.value = caught instanceof Error ? caught.message : '加载下载批次失败' }
    finally { loading.value = false }
  }

  async function create(payload: DownloadBatchCreateRequest): Promise<DownloadBatch | null> {
    working.value = true
    error.value = null
    try { return await downloadBatchApi.create(payload) }
    catch (caught) { error.value = caught instanceof Error ? caught.message : '创建下载批次失败'; return null }
    finally { working.value = false }
  }

  async function retry(batchId: string, itemId: string): Promise<void> {
    working.value = true
    error.value = null
    try { current.value = await downloadBatchApi.retry(batchId, itemId) }
    catch (caught) { error.value = caught instanceof Error ? caught.message : '重试批次条目失败' }
    finally { working.value = false }
  }

  return { batches, current, loading, working, error, load, loadOne, create, retry }
})
