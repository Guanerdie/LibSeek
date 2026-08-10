import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import StatusPill from '../src/components/StatusPill.vue'
import { useApprovalStore } from '../src/stores/approvals'
import { useTorrentStore } from '../src/stores/torrents'

const mocks = vi.hoisted(() => ({
  approvalApprove: vi.fn(),
  approvalPlan: vi.fn(),
  torrentGet: vi.fn(),
  torrentCandidates: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  approvalApi: {
    approve: mocks.approvalApprove,
    plan: mocks.approvalPlan,
  },
  mediaApi: {},
  torrentApi: {
    get: mocks.torrentGet,
    candidates: mocks.torrentCandidates,
  },
}))

function deferred<T>(): {
  promise: Promise<T>
  resolve: (value: T) => void
} {
  let resolve: (value: T) => void = () => undefined
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('torrent search selection reliability', () => {
  it('aborts and ignores an older search response after a newer selection', async () => {
    const oldRun = deferred<{ id: string }>()
    const oldCandidates = deferred<Array<{ id: string }>>()
    const newRun = deferred<{ id: string }>()
    const newCandidates = deferred<Array<{ id: string }>>()

    mocks.torrentGet.mockReturnValueOnce(oldRun.promise).mockReturnValueOnce(newRun.promise)
    mocks.torrentCandidates
      .mockReturnValueOnce(oldCandidates.promise)
      .mockReturnValueOnce(newCandidates.promise)

    const store = useTorrentStore()
    const first = store.selectRun('old-search')
    const oldSignal = mocks.torrentGet.mock.calls[0]?.[1] as AbortSignal
    const second = store.selectRun('new-search')

    expect(oldSignal.aborted).toBe(true)
    newRun.resolve({ id: 'new-search' })
    newCandidates.resolve([{ id: 'new-candidate' }])
    await second

    oldRun.resolve({ id: 'old-search' })
    oldCandidates.resolve([{ id: 'old-candidate' }])
    await first

    expect(store.selectedRun?.id).toBe('new-search')
    expect(store.candidates.map((item) => item.id)).toEqual(['new-candidate'])
    expect(store.error).toBeNull()
    expect(store.selecting).toBe(false)
  })
})

describe('approval partial-success handling', () => {
  it('keeps a successful approval when the follow-up plan read fails', async () => {
    mocks.approvalApprove.mockResolvedValueOnce({ id: 'approval-1', status: 'APPROVED' })
    mocks.approvalPlan.mockRejectedValueOnce(new Error('计划服务暂时不可用'))

    const store = useApprovalStore()
    await store.approve('approval-1')

    expect(store.approval?.status).toBe('APPROVED')
    expect(store.notice).toContain('审批已通过')
    expect(store.error).toBeNull()
    expect(store.plan).toBeNull()
    expect(store.planError).toContain('审批状态已保存')
    expect(store.planError).toContain('计划服务暂时不可用')
  })
})

describe('status presentation', () => {
  it('normalizes every underscore in multi-part state classes', () => {
    const wrapper = mount(StatusPill, { props: { status: 'CANCELLED_BEFORE_ADD' } })
    expect(wrapper.classes()).toContain('state-cancelled-before-add')
  })
})
