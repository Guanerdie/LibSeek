import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AutomationPolicy } from '../src/types'
import AutomationView from '../src/views/AutomationView.vue'

const mocks = vi.hoisted(() => ({
  policy: vi.fn(),
  updatePolicy: vi.fn(),
  jobs: vi.fn(),
  run: vi.fn(),
  retry: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  automationApi: mocks,
}))

const policy: AutomationPolicy = {
  enabled: true,
  dry_run: true,
  auto_identify: true,
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

beforeEach(() => {
  vi.clearAllMocks()
  mocks.policy.mockResolvedValue(policy)
  mocks.updatePolicy.mockImplementation(async (payload) => ({
    ...policy,
    ...payload,
  }))
  mocks.jobs.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 30 })
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

  it('reports budget-deferred jobs separately from successes and failures', async () => {
    mocks.run.mockResolvedValue({ run_id: 'run-1', created: 1, succeeded: 0, failed: 0 })
    const wrapper = mount(AutomationView)
    await flushPromises()

    await wrapper.get('button.button.primary').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('等待后续执行 1 项')
  })
})
