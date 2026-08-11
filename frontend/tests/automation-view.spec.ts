import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import { useAuthStore } from '../src/stores/auth'
import type {
  AutomationDecision,
  AutomationPolicy,
  AutomationPolicyRevision,
} from '../src/types'
import AutomationView from '../src/views/AutomationView.vue'

const mocks = vi.hoisted(() => ({
  policy: vi.fn(),
  revisions: vi.fn(),
  publishRevision: vi.fn(),
  decisions: vi.fn(),
  decision: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  setApiCsrfToken: vi.fn(),
  authApi: {},
  automationApi: mocks,
}))

function makeRevision(revisionNo = 4): AutomationPolicyRevision {
  return {
    id: `revision-${revisionNo}`,
    revision_no: revisionNo,
    identity_mode: 'MANUAL',
    torrent_selection_mode: 'MANUAL',
    approval_mode: 'MANUAL',
    execution_mode: 'MANUAL',
    identity_min_score: 0.5,
    identity_min_margin: 0.1,
    torrent_min_score: 0.75,
    torrent_min_margin: 0.1,
    torrent_min_seeders: 1,
    acknowledges_hnr: false,
    acknowledges_seeding: false,
    acknowledges_plan_only: false,
    acknowledges_add_paused_only: false,
    policy_hash: 'a'.repeat(64),
    previous_policy_hash: 'b'.repeat(64),
    effective_from: '2026-08-11T06:00:00Z',
    created_by: 'admin-user',
    created_at: '2026-08-11T06:00:00Z',
  }
}

function makePolicy(): AutomationPolicy {
  return { scope: 'global', version: 1, engine_enabled: false, revision: makeRevision() }
}

function makeDecision(): AutomationDecision {
  return {
    id: 'decision-1',
    policy_revision_id: 'revision-4',
    stage: 'TORRENT_SELECTION',
    action: 'SELECT_CANDIDATE',
    outcome: 'BLOCKED',
    media_item_id: 'media-1',
    metadata_match_id: null,
    torrent_candidate_id: 'candidate-1',
    approval_request_id: null,
    download_execution_id: null,
    reason_codes: ['HNR_UNKNOWN', 'SCORE_BELOW_THRESHOLD'],
    evidence_snapshot: { score: 0.7, hit_and_run: null },
    evidence_hash: 'e'.repeat(64),
    actor: 'automation-engine',
    created_at: '2026-08-11T06:10:00Z',
  }
}

function primeApi(): void {
  mocks.policy.mockResolvedValue(makePolicy())
  mocks.revisions.mockResolvedValue({ items: [makeRevision()], page: 1, page_size: 20, total: 1 })
  mocks.decisions.mockResolvedValue({ items: [makeDecision()], page: 1, page_size: 20, total: 1 })
  mocks.decision.mockResolvedValue(makeDecision())
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  primeApi()
  const auth = useAuthStore()
  auth.initialized = true
  auth.principal = { username: 'admin-user', role: 'admin' }
})

describe('AutomationView', () => {
  it('renders four fixed mode controls with conservative manual defaults and engine gate', async () => {
    const wrapper = mount(AutomationView)
    await flushPromises()

    expect(wrapper.text()).toContain('自动化引擎已关闭')
    expect(wrapper.findAll('.automation-stage-card')).toHaveLength(4)
    expect(wrapper.findAll('.automation-mode-control button')).toHaveLength(12)
    expect(wrapper.findAll('.automation-mode-control button.active')).toHaveLength(4)
    for (const button of wrapper.findAll('.automation-mode-control button.active')) {
      expect(button.text()).toContain('人工')
    }
    expect(wrapper.text()).toContain('H&R UNKNOWN 会阻断')
    expect(wrapper.text()).not.toContain('立即开始')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('requires the approval safety acknowledgements and publishes only as a new revision', async () => {
    const nextRevision = {
      ...makeRevision(5),
      approval_mode: 'AUTO_IF_ELIGIBLE' as const,
      acknowledges_hnr: true,
      acknowledges_seeding: true,
      acknowledges_plan_only: true,
    }
    mocks.publishRevision.mockResolvedValueOnce({
      scope: 'global',
      version: 5,
      engine_enabled: false,
      revision: nextRevision,
    })
    const wrapper = mount(AutomationView)
    await flushPromises()
    const approvalCard = wrapper
      .findAll('.automation-stage-card')
      .find((card) => card.text().includes('审批与计划'))
    const automatic = approvalCard
      ?.findAll('button')
      .find((button) => button.text().includes('条件自动'))

    await automatic?.trigger('click')
    await nextTick()

    const acknowledgements = wrapper.findAll('.automation-acknowledgements input')
    expect(acknowledgements).toHaveLength(3)
    expect(wrapper.get('.publish-button.primary').attributes('disabled')).toBeDefined()
    for (const checkbox of acknowledgements) await checkbox.setValue(true)
    expect(wrapper.get('.publish-button.primary').attributes('disabled')).toBeUndefined()

    await wrapper.get('.publish-button.primary').trigger('click')
    await flushPromises()

    expect(mocks.publishRevision).toHaveBeenCalledWith(
      expect.objectContaining({
        base_revision_no: 4,
        approval_mode: 'AUTO_IF_ELIGIBLE',
        execution_mode: 'MANUAL',
        acknowledges_hnr: true,
        acknowledges_seeding: true,
        acknowledges_plan_only: true,
        acknowledges_add_paused_only: false,
      }),
    )
    expect(wrapper.text()).toContain('策略修订 #5 已发布')
  })

  it('shows four distinct confirmations when approval and execution are both automatic', async () => {
    const wrapper = mount(AutomationView)
    await flushPromises()
    const cards = wrapper.findAll('.automation-stage-card')
    for (const title of ['审批与计划', '下载执行']) {
      const card = cards.find((item) => item.text().includes(title))
      await card?.findAll('button').find((item) => item.text().includes('条件自动'))?.trigger('click')
    }
    await nextTick()

    expect(wrapper.findAll('.automation-acknowledgements input')).toHaveLength(4)
    expect(wrapper.text()).toContain('只生成不可变计划')
    expect(wrapper.text()).toContain('只允许 ADD_PAUSED')
  })

  it('keeps viewer and operator read-only and reacts to role changes', async () => {
    const auth = useAuthStore()
    auth.principal = { username: 'operator-user', role: 'operator' }
    const wrapper = mount(AutomationView)
    await flushPromises()

    for (const button of wrapper.findAll('.automation-mode-control button')) {
      expect(button.attributes('disabled')).toBeDefined()
    }
    expect(wrapper.get('.publish-button.primary').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('当前角色只读')

    auth.principal = { username: 'admin-user', role: 'admin' }
    await nextTick()
    expect(wrapper.findAll('.automation-mode-control button')[0]?.attributes('disabled')).toBeUndefined()
  })

  it('filters decisions, exposes blocker reasons and loads sanitized evidence by id', async () => {
    const wrapper = mount(AutomationView)
    await flushPromises()

    expect(wrapper.text()).toContain('HNR_UNKNOWN')
    expect(wrapper.text()).toContain('SCORE_BELOW_THRESHOLD')
    await wrapper.get('select[aria-label="决策阶段"]').setValue('TORRENT_SELECTION')
    await wrapper.get('select[aria-label="决策结果"]').setValue('BLOCKED')
    await wrapper.get('input[aria-label="影视 ID"]').setValue('media-1')
    await wrapper.get('.automation-decision-filters').trigger('submit')
    await flushPromises()

    expect(mocks.decisions).toHaveBeenLastCalledWith({
      page: 1,
      pageSize: 20,
      stage: 'TORRENT_SELECTION',
      outcome: 'BLOCKED',
      mediaItemId: 'media-1',
    })
    await wrapper.get('.automation-decision-table .table-action').trigger('click')
    await flushPromises()

    expect(mocks.decision).toHaveBeenCalledWith('decision-1')
    expect(wrapper.text()).toContain('脱敏证据快照')
    expect(wrapper.get('.decision-evidence pre').text()).toContain('"score": 0.7')
  })
})
