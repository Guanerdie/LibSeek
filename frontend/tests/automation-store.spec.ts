import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  conservativeAutomationDraft,
  useAutomationStore,
} from '../src/stores/automation'
import type {
  AutomationDecision,
  AutomationPolicy,
  AutomationPolicyRevision,
} from '../src/types'

const mocks = vi.hoisted(() => ({
  policy: vi.fn(),
  revisions: vi.fn(),
  publishRevision: vi.fn(),
  decisions: vi.fn(),
  decision: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {
    constructor(
      public readonly errorCode: string,
      message: string,
      public readonly status: number,
    ) {
      super(message)
    }
  },
  automationApi: mocks,
}))

function makeRevision(revisionNo = 7): AutomationPolicyRevision {
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
    policy_hash: String(revisionNo).repeat(64),
    previous_policy_hash: revisionNo > 1 ? String(revisionNo - 1).repeat(64) : null,
    effective_from: '2026-08-11T05:00:00Z',
    created_by: 'admin-user',
    created_at: '2026-08-11T05:00:00Z',
  }
}

function makePolicy(revision = makeRevision()): AutomationPolicy {
  return { scope: 'global', version: 1, engine_enabled: false, revision }
}

function makeDecision(id = 'decision-1'): AutomationDecision {
  return {
    id,
    policy_revision_id: 'revision-7',
    stage: 'TORRENT_SELECTION',
    action: 'SELECT_CANDIDATE',
    outcome: 'BLOCKED',
    media_item_id: 'media-1',
    metadata_match_id: null,
    torrent_candidate_id: 'candidate-1',
    approval_request_id: null,
    download_execution_id: null,
    reason_codes: ['HNR_UNKNOWN'],
    evidence_snapshot: { hit_and_run: null },
    evidence_hash: 'e'.repeat(64),
    actor: 'automation-engine',
    created_at: '2026-08-11T05:10:00Z',
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
})

describe('automation store', () => {
  it('uses conservative manual defaults and never inherits publication acknowledgements', () => {
    const published = {
      ...makeRevision(),
      approval_mode: 'AUTO_IF_ELIGIBLE' as const,
      acknowledges_hnr: true,
      acknowledges_seeding: true,
      acknowledges_plan_only: true,
    }

    expect(conservativeAutomationDraft()).toMatchObject({
      identity_mode: 'MANUAL',
      torrent_selection_mode: 'MANUAL',
      approval_mode: 'MANUAL',
      execution_mode: 'MANUAL',
    })
    expect(conservativeAutomationDraft(published)).toMatchObject({
      approval_mode: 'AUTO_IF_ELIGIBLE',
      acknowledges_hnr: false,
      acknowledges_seeding: false,
      acknowledges_plan_only: false,
      acknowledges_add_paused_only: false,
    })
  })

  it('loads the current engine gate and stable revision page contract', async () => {
    const revision = makeRevision()
    mocks.policy.mockResolvedValueOnce(makePolicy(revision))
    mocks.revisions.mockResolvedValueOnce({ items: [revision], page: 2, page_size: 20, total: 22 })
    const store = useAutomationStore()

    await Promise.all([store.loadPolicy(), store.loadRevisions(2)])

    expect(mocks.revisions).toHaveBeenCalledWith({ page: 2, pageSize: 20 })
    expect(store.policy?.engine_enabled).toBe(false)
    expect(store.draft.identity_mode).toBe('MANUAL')
    expect(store.revisionsPage).toBe(2)
    expect(store.revisionsTotal).toBe(22)
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('publishes a flat optimistic revision without actor or immediate-start fields', async () => {
    const revision = makeRevision()
    const nextRevision = {
      ...makeRevision(8),
      approval_mode: 'AUTO_IF_ELIGIBLE' as const,
      acknowledges_hnr: true,
      acknowledges_seeding: true,
      acknowledges_plan_only: true,
    }
    mocks.policy.mockResolvedValueOnce(makePolicy(revision))
    mocks.publishRevision.mockResolvedValueOnce(makePolicy(nextRevision))
    const store = useAutomationStore()
    await store.loadPolicy()
    store.draft.approval_mode = 'AUTO_IF_ELIGIBLE'
    store.draft.acknowledges_hnr = true
    store.draft.acknowledges_seeding = true
    store.draft.acknowledges_plan_only = true

    await expect(store.publish()).resolves.toBe(true)

    expect(mocks.publishRevision).toHaveBeenCalledWith({
      base_revision_no: 7,
      identity_mode: 'MANUAL',
      torrent_selection_mode: 'MANUAL',
      approval_mode: 'AUTO_IF_ELIGIBLE',
      execution_mode: 'MANUAL',
      identity_min_score: 0.5,
      identity_min_margin: 0.1,
      torrent_min_score: 0.75,
      torrent_min_margin: 0.1,
      torrent_min_seeders: 1,
      acknowledges_hnr: true,
      acknowledges_seeding: true,
      acknowledges_plan_only: true,
      acknowledges_add_paused_only: false,
    })
    const body = mocks.publishRevision.mock.calls[0]?.[0] as Record<string, unknown>
    expect(body).not.toHaveProperty('created_by')
    expect(body).not.toHaveProperty('launch_mode')
    expect(JSON.stringify(body)).not.toContain('START_IMMEDIATELY')
    expect(store.policy?.revision.revision_no).toBe(8)
    expect(store.draft.acknowledges_hnr).toBe(false)
  })

  it('surfaces a stale base revision as a publication conflict', async () => {
    const { ApiError } = await import('../src/api/client')
    mocks.policy.mockResolvedValueOnce(makePolicy())
    mocks.publishRevision.mockRejectedValueOnce(
      new ApiError(
        'AUTOMATION_POLICY_REVISION_CONFLICT',
        '策略版本冲突',
        409,
      ),
    )
    const store = useAutomationStore()
    await store.loadPolicy()

    await expect(store.publish()).resolves.toBe(false)

    expect(store.publishConflict).toContain('发布冲突')
    expect(store.publishError).toBeNull()
  })

  it('uses explicit decision filters and rejects a mismatched detail identity', async () => {
    const decision = makeDecision()
    mocks.decisions.mockResolvedValueOnce({ items: [decision], page: 3, page_size: 20, total: 41 })
    mocks.decision.mockResolvedValueOnce({ ...decision, id: 'other-decision' })
    const store = useAutomationStore()

    await store.loadDecisions(
      { stage: 'TORRENT_SELECTION', outcome: 'BLOCKED', mediaItemId: 'media-1' },
      3,
    )
    await store.loadDecision('decision-1')

    expect(mocks.decisions).toHaveBeenCalledWith({
      page: 3,
      pageSize: 20,
      stage: 'TORRENT_SELECTION',
      outcome: 'BLOCKED',
      mediaItemId: 'media-1',
    })
    expect(store.decisions[0]?.reason_codes).toEqual(['HNR_UNKNOWN'])
    expect(store.selectedDecision).toBeNull()
    expect(store.decisionError).toContain('响应不一致')
  })
})
