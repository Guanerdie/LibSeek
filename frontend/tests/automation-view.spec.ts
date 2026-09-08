import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AutomationJob, AutomationPolicy, AutomationRun } from '../src/types'
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
  cooldown_tier_1_hours: 24,
  cooldown_tier_2_hours: 72,
  cooldown_tier_3_hours: 168,
  variety_recent_episodes: 5,
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

function automationJob(overrides: Partial<AutomationJob> = {}): AutomationJob {
  return {
    id: 'job-1',
    run_id: 'run-old',
    media_id: 'media-1',
    media_title: 'Retry Movie',
    state: 'FAILED',
    search_id: null,
    selected_candidate_id: null,
    download_id: null,
    retry_of_job_id: null,
    trigger: 'manual',
    attempt_count: 1,
    next_attempt_at: null,
    decision: {},
    error_message: 'PT 搜索失败',
    created_at: '2026-08-25T00:00:00Z',
    finished_at: '2026-08-25T00:00:01Z',
    superseded_at: null,
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

  it('saves the cooldown ladder the operator typed', async () => {
    const wrapper = mount(AutomationView)
    await flushPromises()

    await wrapper.get('input[name="cooldown_tier_1_hours"]').setValue(12)
    await wrapper.get('input[name="cooldown_tier_2_hours"]').setValue(48)
    await wrapper.get('input[name="cooldown_tier_3_hours"]').setValue(96)
    await wrapper.get('form[aria-labelledby="automation-policy-title"]').trigger('submit')
    await flushPromises()

    expect(mocks.updatePolicy).toHaveBeenCalledWith(
      expect.objectContaining({
        cooldown_tier_1_hours: 12,
        cooldown_tier_2_hours: 48,
        cooldown_tier_3_hours: 96,
      }),
    )
  })

  it('saves how many latest variety episodes to chase', async () => {
    const wrapper = mount(AutomationView)
    await flushPromises()

    await wrapper.get('input[name="variety_recent_episodes"]').setValue(3)
    await wrapper.get('form[aria-labelledby="automation-policy-title"]').trigger('submit')
    await flushPromises()

    expect(mocks.updatePolicy).toHaveBeenCalledWith(
      expect.objectContaining({ variety_recent_episodes: 3 }),
    )
  })

  it('lets the variety window be switched off entirely', async () => {
    const wrapper = mount(AutomationView)
    await flushPromises()

    await wrapper.get('input[name="variety_recent_episodes"]').setValue(0)
    await wrapper.get('form[aria-labelledby="automation-policy-title"]').trigger('submit')
    await flushPromises()

    expect(mocks.updatePolicy).toHaveBeenCalledWith(
      expect.objectContaining({ variety_recent_episodes: 0 }),
    )
  })

  it('fills the cooldown ladder from a preset', async () => {
    const wrapper = mount(AutomationView)
    await flushPromises()

    const aggressive = wrapper
      .findAll('.cooldown-presets .button')
      .find((button) => button.text().startsWith('激进'))
    expect(aggressive).toBeDefined()
    await aggressive!.trigger('click')

    expect(
      (wrapper.get('input[name="cooldown_tier_1_hours"]').element as HTMLInputElement).value,
    ).toBe('6')
    expect(
      (wrapper.get('input[name="cooldown_tier_3_hours"]').element as HTMLInputElement).value,
    ).toBe('72')
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

    expect(mocks.updatePolicy).toHaveBeenCalledWith(expect.objectContaining({ dry_run: true }))
    expect(mocks.updatePolicy.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.run.mock.invocationCallOrder[0],
    )
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

  it('tracks the new run created by an immediate retry', async () => {
    vi.useFakeTimers()
    const failedJob = automationJob()
    const retryJob = automationJob({
      id: 'job-retry',
      run_id: 'run-retry',
      state: 'PENDING',
      retry_of_job_id: failedJob.id,
      error_message: null,
      finished_at: null,
    })
    mocks.jobs.mockResolvedValueOnce({
      items: [failedJob], total: 1, page: 1, page_size: 30,
    }).mockResolvedValue({
      items: [retryJob, { ...failedJob, superseded_at: '2026-08-25T00:01:00Z' }],
      total: 2,
      page: 1,
      page_size: 30,
    })
    mocks.retry.mockResolvedValue(retryJob)
    mocks.runStatus
      .mockRejectedValueOnce(new Error('temporary read failure'))
      .mockResolvedValue(automationRun({ id: 'run-retry' }))

    const wrapper = mount(AutomationView)
    await flushPromises()
    const retryButton = wrapper.findAll('button').find((button) => button.text() === '重新执行')
    expect(retryButton).toBeDefined()

    await retryButton!.trigger('click')
    await flushPromises()

    expect(mocks.updatePolicy).toHaveBeenCalledWith(expect.objectContaining({ dry_run: true }))
    expect(mocks.updatePolicy.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.retry.mock.invocationCallOrder[0],
    )
    expect(mocks.retry).toHaveBeenCalledWith('job-1')
    expect(mocks.runStatus).toHaveBeenCalledWith('run-retry')
    expect(wrapper.text()).toContain('失败任务已立即开始重试')
    expect(wrapper.text()).toContain('无法读取自动搜索进度，将继续重试')

    await vi.advanceTimersByTimeAsync(2_000)
    await flushPromises()

    expect(mocks.runStatus).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('后台执行中…')
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
