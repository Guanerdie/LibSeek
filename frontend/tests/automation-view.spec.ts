import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AutomationPolicy, AutomationRun } from '../src/types'
import AutomationView from '../src/views/AutomationView.vue'

const mocks = vi.hoisted(() => ({
  policy: vi.fn(),
  updatePolicy: vi.fn(),
  jobs: vi.fn(),
  run: vi.fn(),
  latestRun: vi.fn(),
  runStatus: vi.fn(),
  retry: vi.fn(),
  media: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  automationApi: mocks,
  dailyApi: { media: mocks.media },
}))

const policy: AutomationPolicy = {
  enabled: true,
  dry_run: true,
  auto_identify: true,
  scope_mode: 'filters',
  regions: [],
  selected_media_ids: [],
  site_ids: ['avistaz'],
  media_types: ['movie', 'tv'],
  minimum_score: 0.7,
  minimum_seeders: 1,
  max_size_bytes: null,
  allow_warnings: false,
  interval_minutes: 60,
  retry_delay_minutes: 30,
  max_attempts: 3,
  daily_download_limit: 3,
  daily_download_bytes: null,
  updated_at: '2026-08-23T00:00:00Z',
  last_run_at: null,
}

function automationRun(overrides: Partial<AutomationRun> = {}): AutomationRun {
  return {
    id: 'run-1',
    trigger: 'manual',
    state: 'PENDING',
    created: 0,
    succeeded: 0,
    failed: 0,
    deferred: 0,
    error_message: null,
    created_at: '2026-08-25T00:00:00Z',
    started_at: null,
    finished_at: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.policy.mockResolvedValue(policy)
  mocks.updatePolicy.mockImplementation(async (payload) => ({
    ...policy,
    ...payload,
  }))
  mocks.jobs.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 30 })
  mocks.latestRun.mockResolvedValue(null)
  mocks.media.mockResolvedValue({
    items: [], total: 0, page: 1, page_size: 100,
    filter_options: { media_types: [], regions: [], states: [], years: [] },
  })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('automation settings', () => {
  it('requires an explicit policy change before live automatic downloads are requested', async () => {
    const wrapper = mount(AutomationView)
    await flushPromises()

    await wrapper.get('input[name="dry_run"]').setValue(false)
    await wrapper.get('input[name="interval_minutes"]').setValue(120)
    await wrapper.get('input[name="daily_download_limit"]').setValue(2)
    await wrapper.get('form[aria-labelledby="automation-policy-title"]').trigger('submit')
    await flushPromises()

    expect(mocks.updatePolicy).toHaveBeenCalledWith(
      expect.objectContaining({
        enabled: true,
        dry_run: false,
        interval_minutes: 120,
        daily_download_limit: 2,
      }),
    )
    expect(wrapper.text()).toContain('ENABLE_QB_WRITE')
  })

  it('starts a background run, polls it, and refreshes jobs after completion', async () => {
    vi.useFakeTimers()
    mocks.run.mockResolvedValue(automationRun())
    mocks.runStatus.mockResolvedValue(
      automationRun({
        state: 'SUCCEEDED',
        created: 1,
        deferred: 1,
        started_at: '2026-08-25T00:00:01Z',
        finished_at: '2026-08-25T00:00:03Z',
      }),
    )
    const wrapper = mount(AutomationView)
    await flushPromises()

    await wrapper.get('button.button.primary').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('自动搜索已在后台开始')
    expect(wrapper.text()).toContain('后台执行中…')

    await vi.advanceTimersByTimeAsync(2_000)
    await flushPromises()

    expect(mocks.runStatus).toHaveBeenCalledWith('run-1')
    expect(wrapper.text()).toContain('等待后续执行 1 项')
    expect(mocks.jobs).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('resumes the latest active run when the page is opened', async () => {
    vi.useFakeTimers()
    mocks.latestRun.mockResolvedValue(
      automationRun({ state: 'RUNNING', created: 4, succeeded: 1, started_at: '2026-08-25T00:00:01Z' }),
    )
    mocks.runStatus.mockResolvedValue(
      automationRun({
        state: 'SUCCEEDED',
        created: 4,
        succeeded: 3,
        failed: 1,
        started_at: '2026-08-25T00:00:01Z',
        finished_at: '2026-08-25T00:00:08Z',
      }),
    )

    const wrapper = mount(AutomationView)
    await flushPromises()

    expect(wrapper.text()).toContain('已完成 1 / 4 项')
    expect(wrapper.get('button.button.primary').attributes('disabled')).toBeDefined()

    await vi.advanceTimersByTimeAsync(2_000)
    await flushPromises()

    expect(mocks.runStatus).toHaveBeenCalledWith('run-1')
    expect(wrapper.text()).toContain('自动搜索完成：处理 4 项，成功 3 项，失败 1 项')
    expect(mocks.jobs).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('stops polling after the page is unmounted', async () => {
    vi.useFakeTimers()
    mocks.latestRun.mockResolvedValue(automationRun({ state: 'RUNNING', created: 2 }))

    const wrapper = mount(AutomationView)
    await flushPromises()
    wrapper.unmount()

    await vi.advanceTimersByTimeAsync(2_000)

    expect(mocks.runStatus).not.toHaveBeenCalled()
  })
})
