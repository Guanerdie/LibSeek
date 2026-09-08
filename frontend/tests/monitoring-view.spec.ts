import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AutomationStats } from '../src/types'
import MonitoringView from '../src/views/MonitoringView.vue'

const mocks = vi.hoisted(() => ({
  overview: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  statsApi: mocks,
}))

function stats(overrides: Partial<AutomationStats> = {}): AutomationStats {
  return {
    window_days: 7,
    generated_at: '2026-09-08T00:00:00Z',
    search_trend: [
      { date: '2026-09-02', total: 0, succeeded: 0, success_rate: null },
      { date: '2026-09-03', total: 4, succeeded: 2, success_rate: 0.5 },
      { date: '2026-09-04', total: 4, succeeded: 4, success_rate: 1 },
      { date: '2026-09-05', total: 0, succeeded: 0, success_rate: null },
      { date: '2026-09-06', total: 2, succeeded: 1, success_rate: 0.5 },
      { date: '2026-09-07', total: 5, succeeded: 4, success_rate: 0.8 },
      { date: '2026-09-08', total: 5, succeeded: 3, success_rate: 0.6 },
    ],
    site_latency: [
      { site_id: 'avistaz', searches: 12, average_seconds: 6.4, slowest_seconds: 19.2 },
    ],
    recent_runs: [
      {
        id: 'run-1',
        trigger: 'scheduled',
        state: 'SUCCEEDED',
        created_count: 3,
        succeeded_count: 2,
        failed_count: 1,
        deferred_count: 0,
        created_at: '2026-09-08T01:00:00Z',
        duration_seconds: 12.5,
      },
    ],
    library_coverage: {
      total: 10,
      covered: 4,
      coverage_rate: 0.4,
      by_state: { COMPLETE: 3, DOWNLOADING: 1, READY: 6 },
    },
    download_health: { total: 5, errored: 2, by_state: { ERROR: 2, COMPLETED: 3 } },
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('monitoring page', () => {
  it('summarises coverage, weekly success rate and errored downloads', async () => {
    mocks.overview.mockResolvedValueOnce(stats())
    const wrapper = mount(MonitoringView)
    await flushPromises()

    const cards = wrapper.findAll('.summary-card').map((card) => card.text())
    expect(cards[0]).toContain('40%')
    expect(cards[0]).toContain('4 / 10')
    // 14 of 20 finished searches succeeded across the window.
    expect(cards[1]).toContain('70%')
    expect(cards[2]).toContain('2')
  })

  it('draws one point per day that has a rate, and skips the empty days', async () => {
    mocks.overview.mockResolvedValueOnce(stats())
    const wrapper = mount(MonitoringView)
    await flushPromises()

    // Five of the seven days had finished searches.
    expect(wrapper.findAll('.trend-marker')).toHaveLength(5)
    // 9/5 has no searches, so the line is broken there rather than drawn
    // straight through a day that was never measured.
    const segments = wrapper.findAll('.trend-line')
    expect(segments).toHaveLength(2)
    expect(segments[0].attributes('d')).toBeTruthy()
  })

  it('direct-labels the most recent point instead of every point', async () => {
    mocks.overview.mockResolvedValueOnce(stats())
    const wrapper = mount(MonitoringView)
    await flushPromises()

    const labels = wrapper.findAll('.trend-endpoint-label')
    expect(labels).toHaveLength(1)
    expect(labels[0].text()).toBe('60%')
  })

  it('shows a dash rather than a zero when nothing has been searched', async () => {
    mocks.overview.mockResolvedValueOnce(
      stats({
        search_trend: [{ date: '2026-09-08', total: 0, succeeded: 0, success_rate: null }],
        library_coverage: { total: 0, covered: 0, coverage_rate: null, by_state: {} },
      }),
    )
    const wrapper = mount(MonitoringView)
    await flushPromises()

    const cards = wrapper.findAll('.summary-card').map((card) => card.text())
    expect(cards[0]).toContain('—')
    expect(cards[1]).toContain('—')
    expect(wrapper.findAll('.trend-marker')).toHaveLength(0)
  })

  it('scales the latency bar against the slowest site', async () => {
    mocks.overview.mockResolvedValueOnce(
      stats({
        site_latency: [
          { site_id: 'avistaz', searches: 4, average_seconds: 5, slowest_seconds: 9 },
          { site_id: 'other', searches: 2, average_seconds: 10, slowest_seconds: 14 },
        ],
      }),
    )
    const wrapper = mount(MonitoringView)
    await flushPromises()

    const fills = wrapper.findAll('.latency-fill')
    expect(fills[0].attributes('style')).toContain('50%')
    expect(fills[1].attributes('style')).toContain('100%')
    // The value is always written out, never left to the bar length alone.
    expect(wrapper.findAll('.latency-row')[0].text()).toContain('5.0s')
  })

  it('lists run history as a table', async () => {
    mocks.overview.mockResolvedValueOnce(stats())
    const wrapper = mount(MonitoringView)
    await flushPromises()

    const row = wrapper.get('.run-table tbody tr')
    expect(row.text()).toContain('scheduled')
    expect(row.text()).toContain('12.5s')
  })

  it('reports a failure instead of rendering an empty dashboard', async () => {
    mocks.overview.mockRejectedValueOnce(new Error('boom'))
    const wrapper = mount(MonitoringView)
    await flushPromises()

    expect(wrapper.text()).toContain('无法读取运行统计')
    expect(wrapper.find('.summary-card').exists()).toBe(false)
  })
})
