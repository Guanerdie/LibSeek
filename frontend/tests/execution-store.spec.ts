import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useExecutionStore } from '../src/stores/executions'

const mocks = vi.hoisted(() => ({
  createIntent: vi.fn(),
  execute: vi.fn(),
  forApproval: vi.fn(),
  list: vi.fn(),
  get: vi.fn(),
  reconcile: vi.fn(),
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
  executionApi: {
    createIntent: mocks.createIntent,
    execute: mocks.execute,
    forApproval: mocks.forApproval,
    list: mocks.list,
    get: mocks.get,
    reconcile: mocks.reconcile,
  },
}))

const intent = {
  id: 'intent-1',
  approval_id: 'approval-1',
  nonce: `ei1_${'n'.repeat(32)}`,
  status: 'ACTIVE' as const,
  approval_snapshot_hash: 'a'.repeat(64),
  plan_hash: 'b'.repeat(64),
  qb_target_fingerprint: 'c'.repeat(64),
  launch_mode: 'ADD_PAUSED' as const,
  expires_at: '2026-08-11T01:00:00Z',
  created_at: '2026-08-11T00:55:00Z',
}

const execution = {
  id: 'execution-1',
  approval_id: 'approval-1',
  intent_id: 'intent-1',
  status: 'PENDING' as const,
  requires_reconciliation: false,
  approval_snapshot_hash: 'a'.repeat(64),
  plan_hash: 'b'.repeat(64),
  qb_target_fingerprint: 'c'.repeat(64),
  launch_mode: 'ADD_PAUSED' as const,
  attempts: 0,
  max_attempts: 3,
  next_retry_at: null,
  locked_at: null,
  actual_info_hash: null,
  actual_info_hash_v1: null,
  actual_info_hash_v2: null,
  actual_size_bytes: null,
  actual_file_count: null,
  validated_at: null,
  submitted_at: null,
  verified_at: null,
  error_code: null,
  error_message: null,
  requested_by: 'admin-user',
  requested_at: '2026-08-11T00:55:00Z',
  reconciliation_requested_by: null,
  reconciliation_requested_at: null,
  reconciliation_reason: null,
  created_at: '2026-08-11T00:55:00Z',
  updated_at: '2026-08-11T00:55:00Z',
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
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

describe('execution store', () => {
  it('keeps nonce and idempotency material out of state and consumes an intent once', async () => {
    mocks.createIntent.mockResolvedValueOnce(intent)
    mocks.execute.mockResolvedValueOnce(execution)
    const store = useExecutionStore()

    await expect(store.createIntent('approval-1', 'ADD_PAUSED')).resolves.toBe(true)
    expect(store.intent).not.toHaveProperty('nonce')
    expect(JSON.stringify(store.$state)).not.toContain(intent.nonce)

    await expect(store.executeIntent('approval-1')).resolves.toBe(true)
    await expect(store.executeIntent('approval-1')).resolves.toBe(false)

    expect(mocks.execute).toHaveBeenCalledTimes(1)
    expect(mocks.execute).toHaveBeenCalledWith(
      'approval-1',
      'intent-1',
      intent.nonce,
      expect.stringMatching(/^unin-[0-9a-f]{48}$/),
    )
    expect(store.intent).toBeNull()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('never reuses an intent after an uncertain execute response', async () => {
    mocks.createIntent.mockResolvedValueOnce(intent)
    mocks.execute.mockRejectedValueOnce(new Error('网络响应未知'))
    const store = useExecutionStore()
    await store.createIntent('approval-1', 'ADD_PAUSED')

    await expect(store.executeIntent('approval-1')).resolves.toBe(false)
    expect(store.error).toContain('网络响应未知')
    expect(store.approvalLookupStatus).toBe('error')
    expect(store.selected).toBeNull()
    await expect(store.executeIntent('approval-1')).resolves.toBe(false)
    expect(mocks.execute).toHaveBeenCalledTimes(1)
    expect(store.error).toContain('已使用')
  })

  it('invalidates a late execute response when the approval control is reset', async () => {
    const lateExecution = deferred<typeof execution>()
    mocks.createIntent.mockResolvedValueOnce(intent)
    mocks.execute.mockReturnValueOnce(lateExecution.promise)
    const store = useExecutionStore()
    await store.createIntent('approval-1', 'ADD_PAUSED')

    const pending = store.executeIntent('approval-1')
    store.resetControl()
    lateExecution.resolve(execution)

    await expect(pending).resolves.toBe(false)
    expect(store.selected).toBeNull()
    expect(store.intent).toBeNull()
    expect(store.approvalLookupStatus).toBe('idle')
    expect(store.error).toBeNull()
    expect(store.notice).toBeNull()
    expect(store.working).toBe(false)
    expect(store.executions).toEqual([])
  })

  it('rejects an execution intent response bound to another approval', async () => {
    mocks.createIntent.mockResolvedValueOnce({ ...intent, approval_id: 'approval-2' })
    const store = useExecutionStore()

    await expect(store.createIntent('approval-1', 'ADD_PAUSED')).resolves.toBe(false)

    expect(store.intent).toBeNull()
    expect(store.approvalLookupStatus).toBe('error')
    expect(store.error).toContain('响应与当前审批')
  })

  it.each([
    ['approval', { ...execution, approval_id: 'approval-2' }],
    ['intent', { ...execution, intent_id: 'intent-2' }],
  ])('rejects an execute response bound to another %s', async (_binding, response) => {
    mocks.createIntent.mockResolvedValueOnce(intent)
    mocks.execute.mockResolvedValueOnce(response)
    const store = useExecutionStore()
    await store.createIntent('approval-1', 'ADD_PAUSED')

    await expect(store.executeIntent('approval-1')).resolves.toBe(false)

    expect(store.selected).toBeNull()
    expect(store.executions).toEqual([])
    expect(store.approvalLookupStatus).toBe('error')
    expect(store.error).toContain('响应与当前审批或执行意图不一致')
  })

  it('loads stable pages and updates detail after reconciliation', async () => {
    const reconciliation = {
      ...execution,
      status: 'RECONCILIATION_PENDING' as const,
      requires_reconciliation: true,
      reconciliation_requested_by: 'admin-user',
      reconciliation_requested_at: '2026-08-11T01:00:00Z',
      reconciliation_reason: '结果未知',
    }
    mocks.list.mockResolvedValueOnce({ items: [execution], page: 2, page_size: 20, total: 23 })
    mocks.get.mockResolvedValueOnce({ ...execution, requires_reconciliation: true })
    mocks.reconcile.mockResolvedValueOnce(reconciliation)
    const store = useExecutionStore()

    await store.loadList('PENDING', 2)
    expect(mocks.list).toHaveBeenCalledWith({ page: 2, pageSize: 20, status: 'PENDING' })
    expect(store.executions).toHaveLength(1)
    expect(store.page).toBe(2)
    expect(store.total).toBe(23)

    await store.load('execution-1')
    await expect(store.reconcile('execution-1', '结果未知')).resolves.toBe(true)
    expect(store.selected?.status).toBe('RECONCILIATION_PENDING')
    expect(store.notice).toContain('等待后台处理')
  })

  it('loads the execution associated with an approval for resumed review', async () => {
    mocks.forApproval.mockResolvedValueOnce(execution)
    const store = useExecutionStore()

    await store.loadForApproval('approval-1')

    expect(mocks.forApproval).toHaveBeenCalledWith('approval-1')
    expect(store.selected?.id).toBe('execution-1')
    expect(store.error).toBeNull()
  })

  it('only treats the stable no-execution error code as an empty approval lookup', async () => {
    const { ApiError } = await import('../src/api/client')
    mocks.forApproval
      .mockRejectedValueOnce(
        new ApiError(
          'DOWNLOAD_EXECUTION_NOT_FOUND',
          '审批尚未创建下载执行记录',
          404,
        ),
      )
      .mockRejectedValueOnce(
        new ApiError('APPROVAL_REQUEST_NOT_FOUND', '审批请求不存在', 404),
      )
    const store = useExecutionStore()

    await store.loadForApproval('approval-1')
    expect(store.approvalLookupStatus).toBe('not_found')
    expect(store.error).toBeNull()

    await store.loadForApproval('approval-1')
    expect(store.approvalLookupStatus).toBe('error')
    expect(store.error).toBe('审批请求不存在')
  })

  it('fails closed when an approval lookup returns another approval execution', async () => {
    mocks.forApproval.mockResolvedValueOnce({ ...execution, approval_id: 'approval-2' })
    const store = useExecutionStore()

    await store.loadForApproval('approval-1')

    expect(store.selected).toBeNull()
    expect(store.approvalLookupStatus).toBe('error')
    expect(store.error).toContain('响应与当前审批不一致')
  })

  it('surfaces a disabled-control error without creating local success state', async () => {
    mocks.createIntent.mockRejectedValueOnce(new Error('下载执行控制默认关闭'))
    const store = useExecutionStore()

    await expect(store.createIntent('approval-1', 'ADD_PAUSED')).resolves.toBe(false)
    expect(store.error).toBe('下载执行控制默认关闭')
    expect(store.notice).toBeNull()
    expect(store.intent).toBeNull()
    expect(store.selected).toBeNull()
  })
})
