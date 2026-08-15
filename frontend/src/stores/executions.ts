import { defineStore } from 'pinia'
import { ref } from 'vue'

import { ApiError, executionApi } from '../api/client'
import type {
  DownloadExecution,
  DownloadExecutionStatus,
  DownloadJob,
  DownloadLaunchMode,
  ExecutionIntent,
} from '../types'
import { isAbortError, waitForPoll } from '../utils/polling'

type PublicExecutionIntent = Omit<ExecutionIntent, 'nonce'>
type ApprovalExecutionLookupStatus = 'idle' | 'loading' | 'loaded' | 'not_found' | 'error'
type DownloadJobLookupStatus =
  | 'idle'
  | 'polling'
  | 'found'
  | 'not_created'
  | 'timed_out'
  | 'error'

const DOWNLOAD_JOB_POLL_INTERVAL_MS = 2_000
const DOWNLOAD_JOB_POLL_MAX_ATTEMPTS = 30
const DOWNLOAD_JOB_NON_AUTOMATIC_STATUSES = new Set<DownloadExecutionStatus>([
  'FAILED',
  'CANCELLED',
  'OUTCOME_UNKNOWN',
  'RECONCILIATION_REQUIRED',
  'RECONCILIATION_PENDING',
])

interface IntentSecret {
  approvalId: string
  intentId: string
  nonce: string
}

function randomIdempotencyKey(): string {
  const bytes = new Uint8Array(24)
  globalThis.crypto.getRandomValues(bytes)
  return `unin-${Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')}`
}

function isExecutionNotFound(caught: unknown): boolean {
  if (caught instanceof ApiError) {
    return caught.status === 404 && caught.errorCode === 'DOWNLOAD_EXECUTION_NOT_FOUND'
  }
  return (
    typeof caught === 'object' &&
    caught !== null &&
    'status' in caught &&
    caught.status === 404 &&
    'errorCode' in caught &&
    caught.errorCode === 'DOWNLOAD_EXECUTION_NOT_FOUND'
  )
}

function isDownloadJobNotFound(caught: unknown): boolean {
  if (caught instanceof ApiError) {
    return caught.status === 404 && caught.errorCode === 'DOWNLOAD_JOB_NOT_CREATED'
  }
  return (
    typeof caught === 'object' &&
    caught !== null &&
    'status' in caught &&
    caught.status === 404 &&
    'errorCode' in caught &&
    caught.errorCode === 'DOWNLOAD_JOB_NOT_CREATED'
  )
}

function downloadJobNotCreatedMessage(status: DownloadExecutionStatus): string {
  switch (status) {
    case 'FAILED':
      return '下载执行已失败，不会自动生成下载任务；请查看执行错误后重新发起。'
    case 'CANCELLED':
      return '下载执行已取消，不会生成下载任务。'
    case 'OUTCOME_UNKNOWN':
      return '下载提交结果不确定，尚未生成下载任务；请先在执行详情发起人工对账。'
    case 'RECONCILIATION_REQUIRED':
      return '下载执行需要人工对账，当前不会自动生成下载任务。'
    case 'RECONCILIATION_PENDING':
      return '人工对账仍在处理，当前尚未生成下载任务；完成后可重新查询。'
    default:
      return '当前执行状态尚未生成下载任务。'
  }
}

export const useExecutionStore = defineStore('executions', () => {
  const executions = ref<DownloadExecution[]>([])
  const selected = ref<DownloadExecution | null>(null)
  const downloadJob = ref<DownloadJob | null>(null)
  const intent = ref<PublicExecutionIntent | null>(null)
  const page = ref(1)
  const pageSize = ref(20)
  const total = ref(0)
  const loading = ref(false)
  const working = ref(false)
  const error = ref<string | null>(null)
  const notice = ref<string | null>(null)
  const approvalLookupStatus = ref<ApprovalExecutionLookupStatus>('idle')
  const downloadJobLookupStatus = ref<DownloadJobLookupStatus>('idle')
  const downloadJobError = ref<string | null>(null)
  let intentSecret: IntentSecret | null = null
  let generation = 0
  let actionGeneration = 0
  let downloadJobGeneration = 0
  let downloadJobController: AbortController | null = null

  function discardIntent(): void {
    intentSecret = null
    intent.value = null
  }

  function resetControl(): void {
    generation += 1
    actionGeneration += 1
    discardIntent()
    selected.value = null
    loading.value = false
    working.value = false
    error.value = null
    notice.value = null
    approvalLookupStatus.value = 'idle'
    cancelDownloadJobPolling()
  }

  function cancelDownloadJobPolling(): void {
    downloadJobGeneration += 1
    downloadJobController?.abort()
    downloadJobController = null
    downloadJob.value = null
    downloadJobLookupStatus.value = 'idle'
    downloadJobError.value = null
  }

  async function pollDownloadJobForExecution(
    executionId: string,
    executionStatus?: DownloadExecutionStatus,
  ): Promise<boolean> {
    cancelDownloadJobPolling()
    const current = ++downloadJobGeneration
    const controller = new AbortController()
    downloadJobController = controller
    downloadJobLookupStatus.value = 'polling'
    const currentStatus = executionStatus ?? (
      selected.value?.id === executionId ? selected.value.status : undefined
    )
    const pollAttempts = currentStatus && DOWNLOAD_JOB_NON_AUTOMATIC_STATUSES.has(currentStatus)
      ? 1
      : DOWNLOAD_JOB_POLL_MAX_ATTEMPTS

    try {
      for (let attempt = 0; attempt < pollAttempts; attempt += 1) {
        if (attempt > 0) await waitForPoll(DOWNLOAD_JOB_POLL_INTERVAL_MS, controller.signal)
        try {
          const result = await executionApi.downloadJob(executionId, controller.signal)
          if (current !== downloadJobGeneration) return false
          if (result.execution_id !== executionId) {
            throw new Error('下载任务响应与当前执行记录不一致，请刷新后重试')
          }
          downloadJob.value = result
          downloadJobLookupStatus.value = 'found'
          return true
        } catch (caught) {
          if (isAbortError(caught)) return false
          if (!isDownloadJobNotFound(caught)) throw caught
        }
      }

      if (current === downloadJobGeneration) {
        if (currentStatus && DOWNLOAD_JOB_NON_AUTOMATIC_STATUSES.has(currentStatus)) {
          downloadJobLookupStatus.value = 'not_created'
          downloadJobError.value = downloadJobNotCreatedMessage(currentStatus)
        } else {
          downloadJobLookupStatus.value = 'timed_out'
          downloadJobError.value = '暂未生成下载任务；执行器可能仍在处理，可稍后重试。'
        }
      }
      return false
    } catch (caught) {
      if (current === downloadJobGeneration && !isAbortError(caught)) {
        downloadJobLookupStatus.value = 'error'
        downloadJobError.value = caught instanceof Error ? caught.message : '查询关联下载任务失败'
      }
      return false
    } finally {
      if (current === downloadJobGeneration) downloadJobController = null
    }
  }

  async function createIntent(
    approvalId: string,
    launchMode: DownloadLaunchMode,
  ): Promise<boolean> {
    const currentAction = ++actionGeneration
    discardIntent()
    working.value = true
    error.value = null
    notice.value = null
    try {
      const response = await executionApi.createIntent(approvalId, launchMode)
      if (currentAction !== actionGeneration) return false
      if (response.approval_id !== approvalId || response.launch_mode !== launchMode) {
        approvalLookupStatus.value = 'error'
        throw new Error('执行意图响应与当前审批或启动模式不一致，请刷新后重试')
      }
      const { nonce, ...publicIntent } = response
      intentSecret = { approvalId, intentId: response.id, nonce }
      intent.value = publicIntent
      notice.value = '正在准备下载执行'
      return true
    } catch (caught) {
      if (currentAction === actionGeneration) {
        error.value = caught instanceof Error ? caught.message : '创建执行意图失败'
      }
      return false
    } finally {
      if (currentAction === actionGeneration) working.value = false
    }
  }

  async function executeIntent(approvalId: string): Promise<boolean> {
    const secret = intentSecret
    if (!secret || secret.approvalId !== approvalId || secret.intentId !== intent.value?.id) {
      error.value = '执行意图不存在、已使用或已离开当前页面，请重新创建'
      return false
    }

    const currentAction = ++actionGeneration
    // Consume local secret material before the request. An uncertain response must never reuse it.
    discardIntent()
    working.value = true
    error.value = null
    notice.value = null
    selected.value = null
    approvalLookupStatus.value = 'loading'
    try {
      const result = await executionApi.execute(
        approvalId,
        secret.intentId,
        secret.nonce,
        randomIdempotencyKey(),
      )
      if (currentAction !== actionGeneration) return false
      if (result.approval_id !== approvalId || result.intent_id !== secret.intentId) {
        throw new Error('下载执行响应与当前审批或执行意图不一致，请刷新后重试')
      }
      selected.value = result
      approvalLookupStatus.value = 'loaded'
      const existingIndex = executions.value.findIndex((item) => item.id === result.id)
      if (existingIndex >= 0) executions.value.splice(existingIndex, 1, result)
      else {
        executions.value.unshift(result)
        total.value += 1
      }
      notice.value = `下载执行记录已创建，当前状态：${result.status}`
      void pollDownloadJobForExecution(result.id, result.status)
      return true
    } catch (caught) {
      if (currentAction === actionGeneration) {
        selected.value = null
        approvalLookupStatus.value = 'error'
        error.value = caught instanceof Error ? caught.message : '提交下载执行失败'
      }
      return false
    } finally {
      if (currentAction === actionGeneration) working.value = false
    }
  }

  async function loadList(status?: DownloadExecutionStatus, requestedPage = 1): Promise<void> {
    const current = ++generation
    loading.value = true
    error.value = null
    executions.value = []
    page.value = requestedPage
    total.value = 0
    try {
      const result = await executionApi.list({
        page: requestedPage,
        pageSize: pageSize.value,
        status,
      })
      if (current === generation) {
        executions.value = result.items
        page.value = result.page
        pageSize.value = result.page_size
        total.value = result.total
      }
    } catch (caught) {
      if (current === generation) {
        executions.value = []
        total.value = 0
        error.value = caught instanceof Error ? caught.message : '加载下载执行列表失败'
      }
    } finally {
      if (current === generation) loading.value = false
    }
  }

  async function load(executionId: string): Promise<void> {
    cancelDownloadJobPolling()
    const current = ++generation
    loading.value = true
    error.value = null
    notice.value = null
    selected.value = null
    try {
      const result = await executionApi.get(executionId)
      if (current === generation) {
        selected.value = result
        void pollDownloadJobForExecution(result.id, result.status)
      }
    } catch (caught) {
      if (current === generation) {
        selected.value = null
        error.value = caught instanceof Error ? caught.message : '加载下载执行详情失败'
      }
    } finally {
      if (current === generation) loading.value = false
    }
  }

  async function loadForApproval(approvalId: string): Promise<void> {
    cancelDownloadJobPolling()
    const current = ++generation
    actionGeneration += 1
    discardIntent()
    loading.value = true
    working.value = false
    error.value = null
    notice.value = null
    selected.value = null
    approvalLookupStatus.value = 'loading'
    try {
      const result = await executionApi.forApproval(approvalId)
      if (current === generation) {
        if (result.approval_id !== approvalId) {
          throw new Error('下载执行响应与当前审批不一致，请刷新后重试')
        }
        selected.value = result
        approvalLookupStatus.value = 'loaded'
        void pollDownloadJobForExecution(result.id, result.status)
      }
    } catch (caught) {
      if (current === generation) {
        selected.value = null
        if (isExecutionNotFound(caught)) {
          approvalLookupStatus.value = 'not_found'
        } else {
          approvalLookupStatus.value = 'error'
          error.value = caught instanceof Error ? caught.message : '加载审批执行记录失败'
        }
      }
    } finally {
      if (current === generation) loading.value = false
    }
  }

  async function reconcile(executionId: string, reason: string): Promise<boolean> {
    working.value = true
    error.value = null
    notice.value = null
    try {
      const result = await executionApi.reconcile(executionId, reason)
      selected.value = result
      const existingIndex = executions.value.findIndex((item) => item.id === result.id)
      if (existingIndex >= 0) executions.value.splice(existingIndex, 1, result)
      notice.value = '对账请求已提交，等待后台处理'
      return true
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : '提交对账请求失败'
      return false
    } finally {
      working.value = false
    }
  }

  return {
    executions,
    selected,
    downloadJob,
    intent,
    page,
    pageSize,
    total,
    loading,
    working,
    error,
    notice,
    approvalLookupStatus,
    downloadJobLookupStatus,
    downloadJobError,
    createIntent,
    executeIntent,
    discardIntent,
    resetControl,
    pollDownloadJobForExecution,
    cancelDownloadJobPolling,
    loadList,
    load,
    loadForApproval,
    reconcile,
  }
})
