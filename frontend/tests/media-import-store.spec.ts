import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useMediaImportStore } from '../src/stores/mediaImports'
import {
  makeMediaImportCreateRequest,
  makeMediaImportRequest,
  makeMediaImportSummary,
} from './media-import-fixture'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  create: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
  revoke: vi.fn(),
}))

vi.mock('../src/api/client', () => ({ mediaImportApi: mocks }))

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('media import store', () => {
  it('loads a stable paginated list with explicit filters', async () => {
    const item = makeMediaImportSummary()
    mocks.list.mockResolvedValueOnce({ items: [item], page: 2, page_size: 50, total: 51 })
    const store = useMediaImportStore()

    await store.loadList({ status: 'REVIEW_REQUIRED', downloadJobId: ' job-1 ' }, 2)

    expect(mocks.list).toHaveBeenCalledWith({
      page: 2,
      pageSize: 50,
      status: 'REVIEW_REQUIRED',
      downloadJobId: 'job-1',
    })
    expect(store.requests).toEqual([item])
    expect(store.total).toBe(51)
  })

  it('rejects a detail whose request or immutable plan identity does not match', async () => {
    mocks.get.mockResolvedValueOnce({
      ...makeMediaImportRequest(),
      plan: { ...makeMediaImportRequest().plan, request_id: 'other-import' },
    })
    const store = useMediaImportStore()

    await store.loadDetail('import-1')

    expect(store.selected).toBeNull()
    expect(store.detailError).toContain('响应不一致')
  })

  it('creates only a plan-only manifest and mapping payload', async () => {
    const payload = makeMediaImportCreateRequest()
    const created = makeMediaImportRequest('PREFLIGHT_REQUIRED', false)
    mocks.create.mockResolvedValueOnce(created)
    const store = useMediaImportStore()

    await expect(store.create(payload)).resolves.toEqual(created)

    expect(mocks.create).toHaveBeenCalledWith(payload)
    expect(payload.target_mapping).toMatchObject({ source_retention: true, overwrite: false })
    expect(JSON.stringify(payload)).not.toMatch(/execute|scan|move|delete|writeback/i)
  })

  it('maps approve, reject and revoke to their existing endpoints without execution data', async () => {
    const approved = makeMediaImportRequest('APPROVED_PLAN_ONLY')
    mocks.list.mockResolvedValueOnce({
      items: [makeMediaImportSummary()],
      page: 1,
      page_size: 50,
      total: 1,
    })
    mocks.approve.mockResolvedValueOnce(approved)
    mocks.reject.mockResolvedValueOnce(makeMediaImportRequest('REJECTED'))
    mocks.revoke.mockResolvedValueOnce(makeMediaImportRequest('REVOKED'))
    const store = useMediaImportStore()
    const approval = {
      acknowledges_plan_only: true,
      acknowledges_source_retention: true,
      acknowledges_no_overwrite: true,
      acknowledges_hnr: true,
    }

    await store.loadList()

    await expect(store.approve('import-1', approval)).resolves.toBe(true)
    expect(store.requests[0]).toEqual(makeMediaImportSummary(approved))
    expect(store.requests[0]).not.toHaveProperty('plan')

    await expect(store.reject('import-1', ' bad mapping ')).resolves.toBe(true)
    await expect(store.revoke('import-1', ' config changed ')).resolves.toBe(true)

    expect(mocks.approve).toHaveBeenCalledWith('import-1', approval)
    expect(mocks.reject).toHaveBeenCalledWith('import-1', ' bad mapping ')
    expect(mocks.revoke).toHaveBeenCalledWith('import-1', ' config changed ')
  })

  it('does not let a delayed decision response replace a newly selected request', async () => {
    let resolveApproval!: (value: ReturnType<typeof makeMediaImportRequest>) => void
    mocks.get
      .mockResolvedValueOnce(makeMediaImportRequest())
      .mockResolvedValueOnce({
        ...makeMediaImportRequest(),
        id: 'import-2',
        plan: { ...makeMediaImportRequest().plan, request_id: 'import-2' },
      })
    mocks.approve.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveApproval = resolve
      }),
    )
    const store = useMediaImportStore()
    await store.loadDetail('import-1')

    const pendingApproval = store.approve('import-1', {
      acknowledges_plan_only: true,
      acknowledges_source_retention: true,
      acknowledges_no_overwrite: true,
      acknowledges_hnr: true,
    })
    await store.loadDetail('import-2')
    resolveApproval(makeMediaImportRequest('APPROVED_PLAN_ONLY'))
    await pendingApproval

    expect(store.selected?.id).toBe('import-2')
    expect(store.selected?.plan.request_id).toBe('import-2')
    expect(store.actionError).toBeNull()
  })
})
