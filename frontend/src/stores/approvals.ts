import { defineStore } from 'pinia'
import { ref } from 'vue'

import { approvalApi, mediaApi, torrentApi } from '../api/client'
import type {
  ApprovalRequest,
  DownloadPlan,
  MediaItem,
  TorrentCandidateResult,
} from '../types'

export const useApprovalStore = defineStore('approvals', () => {
  const approvals = ref<ApprovalRequest[]>([])
  const approval = ref<ApprovalRequest | null>(null)
  const candidate = ref<TorrentCandidateResult | null>(null)
  const media = ref<MediaItem | null>(null)
  const plan = ref<DownloadPlan | null>(null)
  const planError = ref<string | null>(null)
  const loading = ref(false)
  const working = ref(false)
  const error = ref<string | null>(null)
  const notice = ref<string | null>(null)
  let generation = 0

  function clearDetail(): void {
    approval.value = null
    candidate.value = null
    media.value = null
    plan.value = null
    planError.value = null
  }

  async function loadPlan(approvalId: string, current: number): Promise<void> {
    if (current === generation) planError.value = null
    try {
      const result = await approvalApi.plan(approvalId)
      if (current === generation) {
        plan.value = result
        planError.value = null
      }
    } catch (caught) {
      if (current === generation) {
        plan.value = null
        planError.value = caught instanceof Error
          ? `审批状态已保存，但下载计划读取失败：${caught.message}`
          : '审批状态已保存，但下载计划暂时无法读取'
      }
    }
  }

  async function loadList(): Promise<void> {
    const current = ++generation
    approvals.value = []
    clearDetail()
    loading.value = true
    error.value = null
    try {
      const result = await approvalApi.list()
      if (current === generation) approvals.value = result
    } catch (caught) {
      if (current === generation) {
        approvals.value = []
        error.value = caught instanceof Error ? caught.message : '加载审批列表失败'
      }
    } finally {
      if (current === generation) loading.value = false
    }
  }

  async function loadApproval(approvalId: string): Promise<void> {
    const current = ++generation
    clearDetail()
    loading.value = true
    error.value = null
    notice.value = null
    try {
      const result = await approvalApi.get(approvalId)
      if (current === generation) {
        approval.value = result
      }
      if (
        result.status === 'APPROVED' ||
        result.status === 'EXECUTING' ||
        result.status === 'REVOKED' ||
        result.status === 'CONSUMED'
      ) {
        await loadPlan(approvalId, current)
      }
    } catch (caught) {
      if (current === generation) {
        clearDetail()
        error.value = caught instanceof Error ? caught.message : '加载审批详情失败'
      }
    } finally {
      if (current === generation) loading.value = false
    }
  }

  async function loadCandidate(
    mediaId: string,
    searchId: string,
    candidateId: string,
  ): Promise<void> {
    const current = ++generation
    clearDetail()
    loading.value = true
    error.value = null
    notice.value = null
    try {
      const [mediaResult, candidates, existing] = await Promise.all([
        mediaApi.get(mediaId),
        torrentApi.candidates(searchId),
        approvalApi.list(candidateId),
      ])
      const selected = candidates.find((item) => item.id === candidateId)
      if (!selected) throw new Error('PT 候选不存在或已变更')
      const selectedApproval = existing[0] ?? null
      if (current === generation) {
        media.value = mediaResult
        candidate.value = selected
        approval.value = selectedApproval
      }
      if (
        selectedApproval?.status === 'APPROVED' ||
        selectedApproval?.status === 'EXECUTING' ||
        selectedApproval?.status === 'CONSUMED'
      ) await loadPlan(selectedApproval.id, current)
    } catch (caught) {
      if (current === generation) {
        clearDetail()
        error.value = caught instanceof Error ? caught.message : '加载审批候选失败'
      }
    } finally {
      if (current === generation) loading.value = false
    }
  }

  async function perform(action: () => Promise<ApprovalRequest>, message: string): Promise<void> {
    const current = ++generation
    working.value = true
    error.value = null
    notice.value = null
    planError.value = null
    try {
      const result = await action()
      if (current !== generation) return
      approval.value = result
      notice.value = message
      if (result.status === 'APPROVED') await loadPlan(result.id, current)
    } catch (caught) {
      if (current === generation) {
        plan.value = null
        error.value = caught instanceof Error ? caught.message : '审批操作失败'
      }
    } finally {
      if (current === generation) working.value = false
    }
  }

  const create = (candidateId: string, ttl: number) =>
    perform(() => approvalApi.create(candidateId, ttl), '固定候选审批请求已创建')
  const preflight = (approvalId: string) =>
    perform(() => approvalApi.preflight(approvalId), '只读下载预检已完成')
  const approve = (approvalId: string) =>
    perform(
      () =>
        approvalApi.approve(approvalId, {
          acknowledges_hnr: true,
          acknowledges_seeding: true,
          acknowledges_plan_only: true,
        }),
      '审批已通过，下载计划已生成；若自动执行策略已启用且满足条件，独立 ADD_PAUSED 执行可能已排队',
    )
  const reject = (approvalId: string, reason: string) =>
    perform(() => approvalApi.reject(approvalId, reason), '审批已拒绝')
  const revoke = (approvalId: string, reason: string) =>
    perform(() => approvalApi.revoke(approvalId, reason), '审批已撤销')

  return {
    approvals,
    approval,
    candidate,
    media,
    plan,
    planError,
    loading,
    working,
    error,
    notice,
    loadList,
    loadApproval,
    loadCandidate,
    create,
    preflight,
    approve,
    reject,
    revoke,
  }
})
