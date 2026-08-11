import { defineStore } from 'pinia'
import { ref } from 'vue'

import { mediaImportApi } from '../api/client'
import type {
  MediaImportApproveRequest,
  MediaImportCreateRequest,
  MediaImportRequest,
  MediaImportRequestSummary,
  MediaImportStatus,
} from '../types'

export interface MediaImportListFilters {
  status?: MediaImportStatus
  downloadJobId?: string
}

export const useMediaImportStore = defineStore('media-imports', () => {
  const requests = ref<MediaImportRequestSummary[]>([])
  const selected = ref<MediaImportRequest | null>(null)
  const page = ref(1)
  const pageSize = ref(50)
  const total = ref(0)
  const listLoading = ref(false)
  const detailLoading = ref(false)
  const working = ref(false)
  const listError = ref<string | null>(null)
  const detailError = ref<string | null>(null)
  const actionError = ref<string | null>(null)
  let listGeneration = 0
  let detailGeneration = 0

  async function loadList(
    filters: MediaImportListFilters = {},
    requestedPage = 1,
  ): Promise<void> {
    const current = ++listGeneration
    listLoading.value = true
    listError.value = null
    requests.value = []
    page.value = requestedPage
    total.value = 0
    try {
      const result = await mediaImportApi.list({
        page: requestedPage,
        pageSize: pageSize.value,
        status: filters.status,
        downloadJobId: filters.downloadJobId?.trim() || undefined,
      })
      if (current !== listGeneration) return
      requests.value = result.items
      page.value = result.page
      pageSize.value = result.page_size
      total.value = result.total
    } catch (caught) {
      if (current !== listGeneration) return
      requests.value = []
      total.value = 0
      listError.value = caught instanceof Error ? caught.message : '加载入库规划列表失败'
    } finally {
      if (current === listGeneration) listLoading.value = false
    }
  }

  async function loadDetail(requestId: string): Promise<void> {
    const current = ++detailGeneration
    detailLoading.value = true
    detailError.value = null
    actionError.value = null
    selected.value = null
    try {
      const result = await mediaImportApi.get(requestId)
      if (current !== detailGeneration) return
      if (result.id !== requestId || result.plan.request_id !== requestId) {
        throw new Error('入库规划详情响应不一致，请刷新后重试')
      }
      selected.value = result
    } catch (caught) {
      if (current !== detailGeneration) return
      selected.value = null
      detailError.value = caught instanceof Error ? caught.message : '加载入库规划详情失败'
    } finally {
      if (current === detailGeneration) detailLoading.value = false
    }
  }

  function mergeResult(
    result: MediaImportRequest,
    expectedRequestId?: string,
    expectedDetailGeneration?: number,
  ): void {
    if (
      (expectedRequestId && result.id !== expectedRequestId) ||
      result.plan.request_id !== result.id
    ) {
      throw new Error('入库规划操作响应不一致，请刷新后重试')
    }
    const index = requests.value.findIndex((item) => item.id === result.id)
    if (index >= 0) requests.value[index] = summarizeResult(result)
    if (
      expectedRequestId === undefined ||
      (expectedDetailGeneration === detailGeneration && selected.value?.id === expectedRequestId)
    ) {
      selected.value = result
    }
  }

  function summarizeResult(result: MediaImportRequest): MediaImportRequestSummary {
    const summary = result.plan.job_summary_snapshot
    return {
      id: result.id,
      download_job_id: result.download_job_id,
      media_item_id: result.media_item_id,
      execution_id: result.execution_id,
      status: result.status,
      requested_by: result.requested_by,
      requested_at: result.requested_at,
      updated_at: result.updated_at,
      plan_id: result.plan.id,
      mode: result.plan.mode,
      proposed_operation: result.plan.proposed_operation,
      plan_hash: result.plan.plan_hash,
      media_type: summary.media_type,
      tmdb_id: summary.tmdb_id,
      media_title: summary.media_title,
      media_year: summary.media_year,
      source_root_ref: result.plan.source_manifest.source_root_ref,
      target_root_ref: result.plan.target_mapping.target_root_ref,
      file_count: summary.file_count,
      size_bytes: summary.size_bytes,
      preflight_status: result.preflight?.overall_status ?? null,
      preflight_checked_at: result.preflight?.checked_at ?? null,
      preflight_hash: result.preflight?.preflight_hash ?? null,
    }
  }

  async function create(payload: MediaImportCreateRequest): Promise<MediaImportRequest | null> {
    working.value = true
    actionError.value = null
    try {
      const result = await mediaImportApi.create(payload)
      mergeResult(result)
      return result
    } catch (caught) {
      actionError.value = caught instanceof Error ? caught.message : '创建入库规划失败'
      return null
    } finally {
      working.value = false
    }
  }

  async function approve(
    requestId: string,
    payload: MediaImportApproveRequest,
  ): Promise<boolean> {
    return mutate(
      requestId,
      () => mediaImportApi.approve(requestId, payload),
      '批准入库规划失败',
    )
  }

  async function reject(requestId: string, reason?: string): Promise<boolean> {
    return mutate(requestId, () => mediaImportApi.reject(requestId, reason), '拒绝入库规划失败')
  }

  async function revoke(requestId: string, reason?: string): Promise<boolean> {
    return mutate(requestId, () => mediaImportApi.revoke(requestId, reason), '撤销入库规划失败')
  }

  async function mutate(
    requestId: string,
    action: () => Promise<MediaImportRequest>,
    fallback: string,
  ): Promise<boolean> {
    const currentDetail = detailGeneration
    working.value = true
    actionError.value = null
    try {
      mergeResult(await action(), requestId, currentDetail)
      return true
    } catch (caught) {
      if (currentDetail === detailGeneration && selected.value?.id === requestId) {
        actionError.value = caught instanceof Error ? caught.message : fallback
      }
      return false
    } finally {
      working.value = false
    }
  }

  return {
    requests,
    selected,
    page,
    pageSize,
    total,
    listLoading,
    detailLoading,
    working,
    listError,
    detailError,
    actionError,
    loadList,
    loadDetail,
    create,
    approve,
    reject,
    revoke,
  }
})
