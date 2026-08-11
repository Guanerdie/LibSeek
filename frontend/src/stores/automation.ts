import { defineStore } from 'pinia'
import { ref } from 'vue'

import { ApiError, automationApi } from '../api/client'
import type {
  AutomationDecision,
  AutomationDecisionOutcome,
  AutomationMode,
  AutomationPolicy,
  AutomationPolicyRevision,
  AutomationStage,
  CreateAutomationPolicyRevision,
} from '../types'

export interface AutomationPolicyDraft {
  identity_mode: AutomationMode
  torrent_selection_mode: AutomationMode
  approval_mode: AutomationMode
  execution_mode: AutomationMode
  identity_min_score: number
  identity_min_margin: number
  torrent_min_score: number
  torrent_min_margin: number
  torrent_min_seeders: number
  acknowledges_hnr: boolean
  acknowledges_seeding: boolean
  acknowledges_plan_only: boolean
  acknowledges_add_paused_only: boolean
}

export interface AutomationDecisionFilters {
  stage?: AutomationStage
  outcome?: AutomationDecisionOutcome
  mediaItemId?: string
}

export function conservativeAutomationDraft(
  revision?: AutomationPolicyRevision,
): AutomationPolicyDraft {
  return {
    identity_mode: revision?.identity_mode ?? 'MANUAL',
    torrent_selection_mode: revision?.torrent_selection_mode ?? 'MANUAL',
    approval_mode: revision?.approval_mode ?? 'MANUAL',
    execution_mode: revision?.execution_mode ?? 'MANUAL',
    identity_min_score: revision?.identity_min_score ?? 0.5,
    identity_min_margin: revision?.identity_min_margin ?? 0.1,
    torrent_min_score: revision?.torrent_min_score ?? 0.75,
    torrent_min_margin: revision?.torrent_min_margin ?? 0.1,
    torrent_min_seeders: revision?.torrent_min_seeders ?? 1,
    // Acknowledgements are confirmations for a new immutable revision. Never
    // inherit them into a new publication action or browser storage.
    acknowledges_hnr: false,
    acknowledges_seeding: false,
    acknowledges_plan_only: false,
    acknowledges_add_paused_only: false,
  }
}

export const useAutomationStore = defineStore('automation', () => {
  const policy = ref<AutomationPolicy | null>(null)
  const draft = ref<AutomationPolicyDraft>(conservativeAutomationDraft())
  const revisions = ref<AutomationPolicyRevision[]>([])
  const revisionsPage = ref(1)
  const revisionsPageSize = ref(20)
  const revisionsTotal = ref(0)
  const decisions = ref<AutomationDecision[]>([])
  const selectedDecision = ref<AutomationDecision | null>(null)
  const decisionsPage = ref(1)
  const decisionsPageSize = ref(20)
  const decisionsTotal = ref(0)
  const policyLoading = ref(false)
  const revisionsLoading = ref(false)
  const decisionsLoading = ref(false)
  const decisionLoading = ref(false)
  const publishing = ref(false)
  const policyError = ref<string | null>(null)
  const revisionsError = ref<string | null>(null)
  const decisionsError = ref<string | null>(null)
  const decisionError = ref<string | null>(null)
  const publishError = ref<string | null>(null)
  const publishConflict = ref<string | null>(null)
  const notice = ref<string | null>(null)
  let policyGeneration = 0
  let revisionsGeneration = 0
  let decisionsGeneration = 0
  let decisionGeneration = 0

  async function loadPolicy(): Promise<void> {
    const current = ++policyGeneration
    policyLoading.value = true
    policyError.value = null
    try {
      const result = await automationApi.policy()
      if (current === policyGeneration) {
        policy.value = result
        draft.value = conservativeAutomationDraft(result.revision)
        publishConflict.value = null
        publishError.value = null
        notice.value = null
      }
    } catch (caught) {
      if (current === policyGeneration) {
        policy.value = null
        draft.value = conservativeAutomationDraft()
        policyError.value = caught instanceof Error ? caught.message : '加载自动化策略失败'
      }
    } finally {
      if (current === policyGeneration) policyLoading.value = false
    }
  }

  async function loadRevisions(requestedPage = 1): Promise<void> {
    const current = ++revisionsGeneration
    revisionsLoading.value = true
    revisionsError.value = null
    revisions.value = []
    revisionsPage.value = requestedPage
    revisionsTotal.value = 0
    try {
      const result = await automationApi.revisions({
        page: requestedPage,
        pageSize: revisionsPageSize.value,
      })
      if (current === revisionsGeneration) {
        revisions.value = result.items
        revisionsPage.value = result.page
        revisionsPageSize.value = result.page_size
        revisionsTotal.value = result.total
      }
    } catch (caught) {
      if (current === revisionsGeneration) {
        revisions.value = []
        revisionsTotal.value = 0
        revisionsError.value = caught instanceof Error ? caught.message : '加载策略修订历史失败'
      }
    } finally {
      if (current === revisionsGeneration) revisionsLoading.value = false
    }
  }

  async function publish(): Promise<boolean> {
    if (!policy.value) {
      publishError.value = '当前策略尚未加载，不能发布'
      return false
    }
    publishing.value = true
    publishError.value = null
    publishConflict.value = null
    notice.value = null
    const payload: CreateAutomationPolicyRevision = {
      base_revision_no: policy.value.revision.revision_no,
      ...draft.value,
    }
    try {
      const result = await automationApi.publishRevision(payload)
      const revision = result.revision
      policyGeneration += 1
      policy.value = result
      draft.value = conservativeAutomationDraft(revision)
      const revisionAlreadyLoaded = revisions.value.some((item) => item.id === revision.id)
      revisions.value = [revision, ...revisions.value.filter((item) => item.id !== revision.id)]
      if (!revisionAlreadyLoaded) revisionsTotal.value += 1
      notice.value = `策略修订 #${revision.revision_no} 已发布；自动化引擎开关仍由服务端环境控制。`
      return true
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        caught.status === 409 &&
        caught.errorCode === 'AUTOMATION_POLICY_REVISION_CONFLICT'
      ) {
        publishConflict.value = '发布冲突：当前策略已由其他管理员更新，请刷新后重新核对。'
      } else if (
        caught instanceof ApiError &&
        caught.status === 409 &&
        caught.errorCode === 'AUTOMATION_ACKNOWLEDGEMENTS_REQUIRED'
      ) {
        publishError.value = '自动审批或执行所需的安全确认不完整。'
      } else {
        publishError.value = caught instanceof Error ? caught.message : '发布自动化策略失败'
      }
      return false
    } finally {
      publishing.value = false
    }
  }

  async function loadDecisions(
    filters: AutomationDecisionFilters = {},
    requestedPage = 1,
  ): Promise<void> {
    const current = ++decisionsGeneration
    decisionGeneration += 1
    decisionsLoading.value = true
    decisionsError.value = null
    decisions.value = []
    selectedDecision.value = null
    decisionLoading.value = false
    decisionError.value = null
    decisionsPage.value = requestedPage
    decisionsTotal.value = 0
    try {
      const result = await automationApi.decisions({
        page: requestedPage,
        pageSize: decisionsPageSize.value,
        stage: filters.stage,
        outcome: filters.outcome,
        mediaItemId: filters.mediaItemId,
      })
      if (current === decisionsGeneration) {
        decisions.value = result.items
        decisionsPage.value = result.page
        decisionsPageSize.value = result.page_size
        decisionsTotal.value = result.total
      }
    } catch (caught) {
      if (current === decisionsGeneration) {
        decisions.value = []
        decisionsTotal.value = 0
        decisionsError.value = caught instanceof Error ? caught.message : '加载自动化决策失败'
      }
    } finally {
      if (current === decisionsGeneration) decisionsLoading.value = false
    }
  }

  async function loadDecision(decisionId: string): Promise<void> {
    const current = ++decisionGeneration
    decisionLoading.value = true
    decisionError.value = null
    selectedDecision.value = null
    try {
      const result = await automationApi.decision(decisionId)
      if (current !== decisionGeneration) return
      if (result.id !== decisionId) throw new Error('自动化决策响应不一致')
      selectedDecision.value = result
    } catch (caught) {
      if (current === decisionGeneration) {
        selectedDecision.value = null
        decisionError.value = caught instanceof Error ? caught.message : '加载自动化决策详情失败'
      }
    } finally {
      if (current === decisionGeneration) decisionLoading.value = false
    }
  }

  function resetPublicationState(): void {
    publishError.value = null
    publishConflict.value = null
    notice.value = null
    draft.value = conservativeAutomationDraft(policy.value?.revision)
  }

  return {
    policy,
    draft,
    revisions,
    revisionsPage,
    revisionsPageSize,
    revisionsTotal,
    decisions,
    selectedDecision,
    decisionsPage,
    decisionsPageSize,
    decisionsTotal,
    policyLoading,
    revisionsLoading,
    decisionsLoading,
    decisionLoading,
    publishing,
    policyError,
    revisionsError,
    decisionsError,
    decisionError,
    publishError,
    publishConflict,
    notice,
    loadPolicy,
    loadRevisions,
    publish,
    loadDecisions,
    loadDecision,
    resetPublicationState,
  }
})
