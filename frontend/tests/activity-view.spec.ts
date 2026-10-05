import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ActivityEntry } from '../src/types'
import { detailRows, formatBytes, reasonText } from '../src/utils/activity'
import ActivityView from '../src/views/ActivityView.vue'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  activityApi: mocks,
}))

function entry(overrides: Partial<ActivityEntry> = {}): ActivityEntry {
  return {
    id: 'a1',
    created_at: '2026-10-05T04:00:00Z',
    event: 'DOWNLOAD_CLEANUP_DELETED',
    category: 'cleanup',
    message: '已删除 Example.mkv 的种子和文件',
    actor: 'system:cleanup',
    trigger: 'AUTO',
    reason: '标记于 2026-10-05 10:00，观察期 0 天已过；删除前复核清理标签仍在',
    details: {
      name: 'Example.mkv',
      size_bytes: 5 * 1024 ** 3,
      seeding_days: 21.5,
      required_seeding_days: 10,
      files_deleted: true,
    },
    media_id: 'm1',
    media_title: 'Example Movie',
    ...overrides,
  }
}

function page(items: ActivityEntry[]) {
  return { items, total: items.length, page: 1, page_size: 30 }
}

function mountView() {
  return mount(ActivityView, {
    global: {
      stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('activity page', () => {
  it('lists each action with what, who and how', async () => {
    mocks.list.mockResolvedValueOnce(
      page([
        entry(),
        entry({
          id: 'a2',
          event: 'DOWNLOAD_SUBMITTED',
          category: 'download',
          message: '已提交到 qBittorrent：Other.mkv',
          actor: 'owner',
          trigger: 'MANUAL',
          reason: null,
          details: {},
          media_title: 'Other Show',
        }),
      ]),
    )
    const wrapper = mountView()
    await flushPromises()

    const rows = wrapper.findAll('.activity-row').map((row) => row.text())
    expect(rows).toHaveLength(2)
    expect(rows[0]).toContain('删除种子和文件')
    expect(rows[0]).toContain('Example Movie')
    expect(rows[0]).toContain('空间清理')
    expect(rows[0]).toContain('自动')
    expect(rows[1]).toContain('提交下载')
    expect(rows[1]).toContain('owner')
    expect(rows[1]).toContain('手动')
  })

  it('opens a row into the full record with the reason and readable details', async () => {
    mocks.list.mockResolvedValueOnce(page([entry()]))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.activity-detail').exists()).toBe(false)
    await wrapper.find('.activity-summary').trigger('click')

    const detail = wrapper.find('.activity-detail').text()
    expect(detail).toContain('观察期 0 天已过')
    expect(detail).toContain('5.0 GB')
    expect(detail).toContain('21.5 天')
    expect(detail).toContain('已连同文件删除')
    expect(wrapper.find('.activity-detail a').attributes('href')).toBe('/library/m1/resources')

    await wrapper.find('.activity-summary').trigger('click')
    expect(wrapper.find('.activity-detail').exists()).toBe(false)
  })

  it('sends the chosen filters to the API', async () => {
    mocks.list.mockResolvedValue(page([]))
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('select[aria-label="操作类型"]').setValue('cleanup')
    await wrapper.find('select[aria-label="手动或自动"]').setValue('AUTO')
    await wrapper.find('input[type="search"]').setValue('  Example ')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(mocks.list).toHaveBeenLastCalledWith({
      page: 1,
      pageSize: 30,
      category: 'cleanup',
      trigger: 'AUTO',
      query: 'Example',
    })
    expect(wrapper.text()).toContain('没有符合条件的操作记录')
  })

  it('says so when an old row never recorded who did it', async () => {
    mocks.list.mockResolvedValueOnce(
      page([entry({ actor: null, trigger: null, reason: null, details: {} })]),
    )
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('.activity-summary').trigger('click')

    expect(wrapper.find('.activity-row').text()).toContain('未记录')
    expect(wrapper.find('.activity-detail').text()).toContain('还没有记录原因')
  })
})

describe('activity helpers', () => {
  it('shows a policy change as old value to new value', () => {
    expect(
      detailRows({ changes: { cleanup_grace_days: { from: 2, to: 0 }, cleanup_dry_run: { from: true, to: false } } }),
    ).toEqual([
      { key: 'changes.cleanup_grace_days', label: '标记后观察天数', value: '2 → 0' },
      { key: 'changes.cleanup_dry_run', label: '清理演练模式', value: '是 → 否' },
    ])
  })

  it('formats sizes and explains an action that needs no reason', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(250 * 1024 ** 3)).toBe('250 GB')
    expect(reasonText(entry({ trigger: 'MANUAL', reason: null }))).toBe('用户手动操作')
  })
})
