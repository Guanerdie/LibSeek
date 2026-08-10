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
      let downloadPlan: DownloadPlan | null = null
      if (result.status === 'APPROVED' || result.status === 'REVOKED' || result.status === 'CONSUMED') {
        try {
          downloadPlan = await approvalApi.plan(approvalId)
        } catch {
          downloadPlan = null
        }
      }
      if (current === generation) {
        approval.value = result
        plan.value = downloadPlan
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
      const existingPlan =
        selectedApproval?.status === 'APPROVED'
          ? await approvalApi.plan(selectedApproval.id)
          : null
      if (current === generation) {
        media.value = mediaResult
        candidate.value = selected
        approval.value = selectedApproval
        plan.value = existingPlan
      }
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
    try {
      const result = await action()
      const resultPlan = result.status === 'APPROVED' ? await approvalApi.plan(result.id) : null
      if (current !== generation) return
      approval.value = result
      plan.value = resultPlan
      notice.value = message
    } catch (caught) {
      if (current === generation) {
        plan.value = null
        error.value = caught instanceof Error ? caught.message : '审批操作失败'
      }
    } finally {
      if (current === generation) working.value = false
    }
  }

  const create = (candidateId: string, operator: string, ttl: number) =>
    perform(() => approvalApi.create(candidateId, operator, ttl), '固定候选审批请求已创建')
  const preflight = (approvalId: string, operator: string) =>
    perform(() => approvalApi.preflight(approvalId, operator), '只读下载预检已完成')
  const approve = (approvalId: string, operator: string) =>
    perform(
      () =>
        approvalApi.approve(approvalId, {
          operator,
          acknowledges_hnr: true,
          acknowledges_seeding: true,
          acknowledges_plan_only: true,
        }),
      '审批已通过，仅生成下载计划',
    )
  const reject = (approvalId: string, operator: string, reason: string) =>
    perform(() => approvalApi.reject(approvalId, operator, reason), '审批已拒绝')
  const revoke = (approvalId: string, operator: string, reason: string) =>
    perform(() => approvalApi.revoke(approvalId, operator, reason), '审批已撤销')

  return {
    approvals,
    approval,
    candidate,
    media,
    plan,
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
