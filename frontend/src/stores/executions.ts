import { defineStore } from 'pinia'
import { ref } from 'vue'

import { executionApi } from '../api/client'
import type {
  DownloadExecution,
  DownloadExecutionStatus,
  DownloadLaunchMode,
  ExecutionIntent,
} from '../types'

type PublicExecutionIntent = Omit<ExecutionIntent, 'nonce'>

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

export const useExecutionStore = defineStore('executions', () => {
  const executions = ref<DownloadExecution[]>([])
  const selected = ref<DownloadExecution | null>(null)
  const intent = ref<PublicExecutionIntent | null>(null)
  const page = ref(1)
  const pageSize = ref(20)
  const total = ref(0)
  const loading = ref(false)
  const working = ref(false)
  const error = ref<string | null>(null)
  const notice = ref<string | null>(null)
  let intentSecret: IntentSecret | null = null
  let generation = 0

  function discardIntent(): void {
    intentSecret = null
    intent.value = null
  }

  function resetControl(): void {
    discardIntent()
    selected.value = null
    error.value = null
    notice.value = null
  }

  async function createIntent(
    approvalId: string,
    launchMode: DownloadLaunchMode,
  ): Promise<boolean> {
    discardIntent()
    working.value = true
    error.value = null
    notice.value = null
    try {
      const response = await executionApi.createIntent(approvalId, launchMode)
      const { nonce, ...publicIntent } = response
      intentSecret = { approvalId, intentId: response.id, nonce }
      intent.value = publicIntent
      notice.value = '执行意图已创建，尚未提交到下载器；请完成第二步确认'
      return true
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : '创建执行意图失败'
      return false
    } finally {
      working.value = false
    }
  }

  async function executeIntent(approvalId: string): Promise<boolean> {
    const secret = intentSecret
    if (!secret || secret.approvalId !== approvalId || secret.intentId !== intent.value?.id) {
      error.value = '执行意图不存在、已使用或已离开当前页面，请重新创建'
      return false
    }

    // Consume local secret material before the request. An uncertain response must never reuse it.
    discardIntent()
    working.value = true
    error.value = null
    notice.value = null
    try {
      const result = await executionApi.execute(
        approvalId,
        secret.intentId,
        secret.nonce,
        randomIdempotencyKey(),
      )
      selected.value = result
      const existingIndex = executions.value.findIndex((item) => item.id === result.id)
      if (existingIndex >= 0) executions.value.splice(existingIndex, 1, result)
      else {
        executions.value.unshift(result)
        total.value += 1
      }
      notice.value = `下载执行记录已创建，当前状态：${result.status}`
      return true
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : '提交下载执行失败'
      return false
    } finally {
      working.value = false
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
    const current = ++generation
    loading.value = true
    error.value = null
    notice.value = null
    selected.value = null
    try {
      const result = await executionApi.get(executionId)
      if (current === generation) selected.value = result
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
    const current = ++generation
    loading.value = true
    error.value = null
    selected.value = null
    try {
      const result = await executionApi.forApproval(approvalId)
      if (current === generation) selected.value = result
    } catch (caught) {
      if (current === generation) {
        selected.value = null
        error.value = caught instanceof Error ? caught.message : '加载审批执行记录失败'
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
    intent,
    page,
    pageSize,
    total,
    loading,
    working,
    error,
    notice,
    createIntent,
    executeIntent,
    discardIntent,
    resetControl,
    loadList,
    load,
    loadForApproval,
    reconcile,
  }
})
