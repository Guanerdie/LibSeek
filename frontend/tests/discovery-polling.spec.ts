import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '../src/stores/auth'
import { useDiscoveryStore } from '../src/stores/discovery'
import type { DiscoveryRun } from '../src/types'
import DiscoveryView from '../src/views/DiscoveryView.vue'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  create: vi.fn(),
}))

vi.mock('../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return {
    ...actual,
    discoveryApi: {
      list: mocks.list,
      get: mocks.get,
      create: mocks.create,
    },
  }
})

const run: DiscoveryRun = {
  id: 'run-1',
  source: 'nextfind',
  status: 'RUNNING',
  started_at: '2026-08-12T00:00:00Z',
  finished_at: null,
  discovered_count: 0,
  created_count: 0,
  updated_count: 0,
  error_code: null,
  error_message: null,
  created_at: '2026-08-12T00:00:00Z',
}

function pageWith(item: DiscoveryRun) {
  return { items: [item], page: 1, page_size: 20, total: 1 }
}

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('discovery polling', () => {
  it('runs requests serially and ignores a response after polling is stopped', async () => {
    let resolveSlowPoll: (value: ReturnType<typeof pageWith>) => void = () => undefined
    mocks.list
      .mockResolvedValueOnce(pageWith(run))
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveSlowPoll = resolve
      }))

    const store = useDiscoveryStore()
    const polling = store.startAutoRefresh()
    await flushPromises()

    expect(store.autoRefreshing).toBe(true)
    expect(mocks.list).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1_500)
    expect(mocks.list).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(6_000)
    expect(mocks.list).toHaveBeenCalledTimes(2)

    store.stopAutoRefresh()
    resolveSlowPoll(pageWith({ ...run, status: 'SUCCEEDED' }))
    await polling

    expect(store.autoRefreshing).toBe(false)
    expect(store.runs[0]?.status).toBe('RUNNING')
  })

  it('shows retry wait state and cancels polling when the view unmounts', async () => {
    mocks.list.mockResolvedValue(pageWith({ ...run, status: 'RETRY_WAIT' }))
    const auth = useAuthStore()
    auth.principal = { username: 'operator-user', role: 'operator' }

    const wrapper = mount(DiscoveryView)
    await flushPromises()

    expect(wrapper.get('[data-testid="discovery-polling-status"]').text()).toContain(
      '等待 Worker 重试',
    )
    expect(mocks.create).not.toHaveBeenCalled()
    expect(mocks.list).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(3_000)

    expect(useDiscoveryStore().autoRefreshing).toBe(false)
    expect(mocks.list).toHaveBeenCalledTimes(1)
    expect(mocks.create).not.toHaveBeenCalled()
  })

  it('keeps manual refresh available and resumes tracking active work', async () => {
    mocks.list
      .mockResolvedValueOnce(pageWith({ ...run, status: 'SUCCEEDED' }))
      .mockResolvedValueOnce(pageWith(run))
      .mockResolvedValueOnce(pageWith({ ...run, status: 'SUCCEEDED' }))

    const wrapper = mount(DiscoveryView)
    await flushPromises()
    expect(mocks.list).toHaveBeenCalledTimes(1)

    await wrapper.get('.header-actions .secondary').trigger('click')
    await flushPromises()
    expect(mocks.list).toHaveBeenCalledTimes(2)
    expect(useDiscoveryStore().autoRefreshing).toBe(true)

    await vi.advanceTimersByTimeAsync(1_500)
    await flushPromises()
    expect(mocks.list).toHaveBeenCalledTimes(3)
    expect(useDiscoveryStore().autoRefreshing).toBe(false)

    wrapper.unmount()
  })
})
