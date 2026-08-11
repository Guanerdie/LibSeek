import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import { useAuthStore } from '../src/stores/auth'
import ExecutionDetailView from '../src/views/ExecutionDetailView.vue'
import ExecutionListView from '../src/views/ExecutionListView.vue'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  reconcile: vi.fn(),
  createIntent: vi.fn(),
  execute: vi.fn(),
  forApproval: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  setApiCsrfToken: vi.fn(),
  authApi: {},
  executionApi: {
    list: mocks.list,
    get: mocks.get,
    reconcile: mocks.reconcile,
    createIntent: mocks.createIntent,
    execute: mocks.execute,
    forApproval: mocks.forApproval,
  },
}))

const execution = {
  id: 'execution-1',
  approval_id: 'approval-1',
  intent_id: 'intent-1',
  status: 'RECONCILIATION_REQUIRED' as const,
  requires_reconciliation: true,
  approval_snapshot_hash: 'a'.repeat(64),
  plan_hash: 'b'.repeat(64),
  qb_target_fingerprint: 'c'.repeat(64),
  launch_mode: 'ADD_PAUSED' as const,
  attempts: 1,
  max_attempts: 3,
  next_retry_at: null,
  locked_at: null,
  actual_info_hash: null,
  actual_info_hash_v1: null,
  actual_info_hash_v2: null,
  actual_size_bytes: null,
  actual_file_count: null,
  validated_at: '2026-08-11T00:57:00Z',
  submitted_at: null,
  verified_at: null,
  error_code: 'QB_OUTCOME_UNKNOWN',
  error_message: '提交结果未知，需要人工对账',
  requested_by: 'admin-user',
  requested_at: '2026-08-11T00:55:00Z',
  reconciliation_requested_by: null,
  reconciliation_requested_at: null,
  reconciliation_reason: null,
  created_at: '2026-08-11T00:55:00Z',
  updated_at: '2026-08-11T00:57:00Z',
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  const auth = useAuthStore()
  auth.initialized = true
  auth.principal = { username: 'viewer-user', role: 'viewer' }
})

describe('execution views', () => {
  it('renders a stable page and requests the next page explicitly', async () => {
    mocks.list
      .mockResolvedValueOnce({ items: [execution], page: 1, page_size: 20, total: 21 })
      .mockResolvedValueOnce({ items: [], page: 2, page_size: 20, total: 21 })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/executions', component: ExecutionListView },
        { path: '/executions/:id', component: ExecutionDetailView },
      ],
    })
    await router.push('/executions')
    const wrapper = mount(ExecutionListView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('QB_OUTCOME_UNKNOWN')
    expect(wrapper.text()).toContain('需要人工对账')
    expect(wrapper.text()).toContain('共 21 条')
    await wrapper.get('.pagination button:last-child').trigger('click')
    await flushPromises()
    expect(mocks.list).toHaveBeenLastCalledWith({ page: 2, pageSize: 20, status: undefined })
  })

  it('keeps reconciliation viewer-readable but admin-only and reacts to role changes', async () => {
    mocks.get.mockResolvedValueOnce(execution)
    mocks.reconcile.mockResolvedValueOnce({
      ...execution,
      status: 'RECONCILIATION_PENDING',
      reconciliation_requested_by: 'admin-user',
      reconciliation_requested_at: '2026-08-11T01:00:00Z',
      reconciliation_reason: '核对 qB 状态',
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/executions', component: ExecutionListView },
        { path: '/executions/:id', component: ExecutionDetailView },
      ],
    })
    await router.push('/executions/execution-1')
    const wrapper = mount(ExecutionDetailView, { global: { plugins: [router] } })
    await flushPromises()

    const reconcileButton = wrapper.findAll('button').find((item) => item.text().includes('请求对账'))
    expect(wrapper.text()).toContain('提交结果未知，需要人工对账')
    expect(reconcileButton?.attributes('disabled')).toBeDefined()

    const auth = useAuthStore()
    auth.principal = { username: 'admin-user', role: 'admin' }
    await nextTick()
    expect(reconcileButton?.attributes('disabled')).toBeUndefined()
    await wrapper.get('.execution-reconcile textarea').setValue('核对 qB 状态')
    await reconcileButton?.trigger('click')
    await flushPromises()

    expect(mocks.reconcile).toHaveBeenCalledWith('execution-1', '核对 qB 状态')
    expect(wrapper.text()).toContain('等待后台处理')
  })
})
