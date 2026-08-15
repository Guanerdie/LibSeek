import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '../src/stores/auth'
import type { ApprovalRequest } from '../src/types'
import ApprovalView from '../src/views/ApprovalView.vue'

const mocks = vi.hoisted(() => ({
  approvalGet: vi.fn(),
  approvalList: vi.fn(),
  approvalCreate: vi.fn(),
  approvalPreflight: vi.fn(),
  approvalApprove: vi.fn(),
  approvalReject: vi.fn(),
  approvalRevoke: vi.fn(),
  approvalPlan: vi.fn(),
  mediaGet: vi.fn(),
  torrentCandidates: vi.fn(),
  executionCreateIntent: vi.fn(),
  executionExecute: vi.fn(),
  executionForApproval: vi.fn(),
  executionList: vi.fn(),
  executionGet: vi.fn(),
  executionDownloadJob: vi.fn(),
  executionReconcile: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  setApiCsrfToken: vi.fn(),
  authApi: {},
  mediaApi: { get: mocks.mediaGet },
  torrentApi: { candidates: mocks.torrentCandidates },
  approvalApi: {
    get: mocks.approvalGet,
    list: mocks.approvalList,
    create: mocks.approvalCreate,
    preflight: mocks.approvalPreflight,
    approve: mocks.approvalApprove,
    reject: mocks.approvalReject,
    revoke: mocks.approvalRevoke,
    plan: mocks.approvalPlan,
  },
  executionApi: {
    createIntent: mocks.executionCreateIntent,
    execute: mocks.executionExecute,
    forApproval: mocks.executionForApproval,
    list: mocks.executionList,
    get: mocks.executionGet,
    downloadJob: mocks.executionDownloadJob,
    reconcile: mocks.executionReconcile,
  },
}))

function approval(id: string, title: string): ApprovalRequest {
  return {
    id,
    media_item_id: `media-${id}`,
    torrent_candidate_id: `candidate-${id}`,
    status: 'PENDING',
    candidate: {
      media_item_id: `media-${id}`,
      media_title: title,
      media_type: 'tv',
      tmdb_id: 42,
      year: 2026,
      torrent_candidate_id: `candidate-${id}`,
      site_id: 'avistaz',
      torrent_id: `torrent-${id}`,
      torrent_ref: `avistaz:details:${id}`,
      release_title: `${title} S01E03 1080p WEB-DL`,
      size_bytes: 2048,
      info_hash: '1'.repeat(40),
      season: 1,
      episodes: [3],
      resolution: '1080p',
      source: 'WEB-DL',
      subtitles: ['Chinese'],
      seeders: 4,
      promotion: { download_factor: 0, upload_factor: 1 },
      hit_and_run: false,
      match_score: 0.95,
      match_reasons: ['TMDB_ID_EXACT'],
      warnings: [],
      requested_at: '2026-08-11T00:00:00Z',
      expires_at: '2026-08-11T01:00:00Z',
    },
    snapshot_hash: 'a'.repeat(64),
    requested_by: 'operator-user',
    requested_at: '2026-08-11T00:00:00Z',
    expires_at: '2026-08-11T01:00:00Z',
    decided_at: null,
    preflight_result: {
      overall_status: 'PASS',
      checks: [{ code: 'READY', status: 'PASS', message: '预检通过', details: {} }],
      checked_at: '2026-08-11T00:01:00Z',
      policy_fingerprint: 'b'.repeat(64),
    },
    preflight_checked_at: '2026-08-11T00:01:00Z',
    events: [],
  }
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve: (value: T) => void = () => undefined
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}

function noExecution(): Error & { status: number; errorCode: string } {
  return Object.assign(new Error('审批尚未创建下载执行记录'), {
    status: 404,
    errorCode: 'DOWNLOAD_EXECUTION_NOT_FOUND',
  })
}

async function approvalRouter(initialId = 'approval-a') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/approvals/:id', component: ApprovalView }],
  })
  await router.push(`/approvals/${initialId}`)
  await router.isReady()
  return router
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  mocks.executionDownloadJob.mockRejectedValue(new Error('关联下载任务接口暂不可用'))
  mocks.executionForApproval.mockRejectedValue(noExecution())
  const auth = useAuthStore()
  auth.principal = { username: 'admin-user', role: 'admin' }
  auth.initialized = true
})

describe('ApprovalView route reuse', () => {
  it('loads the new approval and ignores a late response from the previous route', async () => {
    const oldApproval = deferred<ApprovalRequest>()
    mocks.approvalGet
      .mockReturnValueOnce(oldApproval.promise)
      .mockResolvedValueOnce(approval('approval-b', '当前审批'))
    const router = await approvalRouter()
    const wrapper = mount(ApprovalView, { global: { plugins: [router] } })
    await flushPromises()

    await router.push('/approvals/approval-b')
    await flushPromises()
    expect(mocks.approvalGet).toHaveBeenNthCalledWith(2, 'approval-b')
    expect(wrapper.text()).toContain('当前审批')

    oldApproval.resolve(approval('approval-a', '迟到旧审批'))
    await flushPromises()

    expect(wrapper.text()).toContain('当前审批')
    expect(wrapper.text()).not.toContain('迟到旧审批')
    expect(mocks.executionForApproval).toHaveBeenCalledTimes(1)
    expect(mocks.executionForApproval).toHaveBeenCalledWith('approval-b')
  })

  it('clears acknowledgement and execution confirmations for the reused route', async () => {
    mocks.approvalGet
      .mockResolvedValueOnce(approval('approval-a', '审批 A'))
      .mockResolvedValueOnce(approval('approval-b', '审批 B'))
    const router = await approvalRouter()
    const wrapper = mount(ApprovalView, { global: { plugins: [router] } })
    await flushPromises()

    for (const checkbox of wrapper.findAll('.acknowledgements input[type="checkbox"]')) {
      await checkbox.setValue(true)
    }
    const findApprove = () =>
      wrapper.findAll('button').find((button) => button.text().includes('预检并批准计划'))
    expect(findApprove()?.attributes('disabled')).toBeUndefined()

    await router.push('/approvals/approval-b')
    await flushPromises()

    expect(wrapper.text()).toContain('审批 B')
    expect(wrapper.text()).not.toContain('审批 A')
    for (const checkbox of wrapper.findAll('.acknowledgements input[type="checkbox"]')) {
      expect((checkbox.element as HTMLInputElement).checked).toBe(false)
    }
    expect(findApprove()?.attributes('disabled')).toBeDefined()
  })

  it('clears a revoked plan and replaces a direct approval URL after reissuing', async () => {
    const revoked = { ...approval('approval-a', '旧审批'), status: 'REVOKED' as const }
    const created = {
      ...approval('approval-new', '重发审批'),
      media_item_id: revoked.media_item_id,
      torrent_candidate_id: revoked.torrent_candidate_id,
      candidate: {
        ...revoked.candidate,
        media_title: '重发审批',
      },
    }
    const oldPlan = {
      id: 'plan-old',
      approval_id: revoked.id,
      approval_snapshot_hash: 'a'.repeat(64),
      preflight_policy_fingerprint: 'b'.repeat(64),
      plan_hash: 'c'.repeat(64),
      site_id: 'avistaz',
      torrent_ref: 'avistaz:details:old',
      expected_info_hash: '1'.repeat(40),
      release_title: '旧审批下载计划',
      save_path_ref: 'media-tv',
      category: 'media',
      tags: ['unin'],
      estimated_size_bytes: 2048,
      media_destination_plan: {},
      preflight_result: revoked.preflight_result,
      warnings: [],
      created_at: '2026-08-11T00:02:00Z',
    }
    const pendingCreate = deferred<ApprovalRequest>()
    mocks.approvalGet
      .mockResolvedValueOnce(revoked)
      .mockResolvedValueOnce(created)
    mocks.approvalPlan.mockResolvedValueOnce(oldPlan)
    mocks.approvalCreate.mockReturnValueOnce(pendingCreate.promise)
    const router = await approvalRouter()
    const wrapper = mount(ApprovalView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('avistaz:details:old')
    const reissue = wrapper.findAll('button').find((button) => button.text().includes('重新发起审批'))
    await reissue?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('avistaz:details:old')
    expect(mocks.approvalCreate).toHaveBeenCalledWith(revoked.torrent_candidate_id, 60)

    pendingCreate.resolve(created)
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/approvals/approval-new')
    expect(mocks.approvalGet).toHaveBeenLastCalledWith('approval-new')
    expect(wrapper.text()).toContain('重发审批')
    expect(wrapper.text()).not.toContain('avistaz:details:old')
  })
})
