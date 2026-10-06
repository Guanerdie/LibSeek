import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AutomationPolicy, CleanupPreview, CleanupPreviewEntry } from '../src/types'
import CleanupView from '../src/views/CleanupView.vue'

const mocks = vi.hoisted(() => ({
  policy: vi.fn(),
  updateCleanupPolicy: vi.fn(),
  cleanupPreview: vi.fn(),
  setCleanupHold: vi.fn(),
  setCleanupHolds: vi.fn(),
  list: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  automationApi: { policy: mocks.policy, updateCleanupPolicy: mocks.updateCleanupPolicy },
  dailyApi: {
    cleanupPreview: mocks.cleanupPreview,
    setCleanupHold: mocks.setCleanupHold,
    setCleanupHolds: mocks.setCleanupHolds,
  },
  activityApi: { list: mocks.list },
}))

function policy(overrides: Partial<AutomationPolicy> = {}): AutomationPolicy {
  return {
    cleanup_enabled: false,
    cleanup_dry_run: true,
    cleanup_after_days: 10,
    cleanup_min_seeding_days: 10,
    cleanup_grace_days: 0,
    cleanup_require_library_confirmed: true,
    cleanup_daily_limit: 20,
    cleanup_release_backlog: false,
    ...overrides,
  } as AutomationPolicy
}

function item(overrides: Partial<CleanupPreviewEntry> = {}): CleanupPreviewEntry {
  return {
    download_id: 'd1',
    media_title: '蓝色情结',
    name: 'Blue.Complex.S01.1080p',
    size_bytes: 2 * 1024 ** 3,
    completed_at: '2026-09-15T00:00:00Z',
    seeding_days: 19.6,
    required_seeding_days: 10,
    cleanup_state: 'HELD',
    deletes_at: null,
    blocked_reason: null,
    ...overrides,
  }
}

function preview(items: CleanupPreviewEntry[], overrides: Partial<CleanupPreview> = {}): CleanupPreview {
  return {
    enabled: false,
    dry_run: true,
    delete_authorized: true,
    reclaimable_bytes: 0,
    held_reclaimable_bytes: 0,
    items,
    ...overrides,
  }
}

function mountView() {
  return mount(CleanupView, {
    global: {
      stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } },
    },
  })
}

function cards(wrapper: ReturnType<typeof mountView>): string[] {
  return wrapper.findAll('.summary-card').map((card) => card.text())
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.policy.mockResolvedValue(policy())
  mocks.list.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 8 })
})

describe('cleanup page', () => {
  it('shows what can be released without any click, smallest first', async () => {
    mocks.cleanupPreview.mockResolvedValue(
      preview([
        item({ download_id: 'big', media_title: '红宝石戒指', size_bytes: 200 * 1024 ** 3 }),
        item(),
        item({ download_id: 'd3', media_title: '大物', blocked_reason: '尚未确认入库' }),
      ]),
    )
    const wrapper = mountView()
    await flushPromises()

    expect(cards(wrapper)[0]).toContain('未启用')
    expect(cards(wrapper)[1]).toContain('0 项')
    expect(cards(wrapper)[2]).toContain('2 项')
    expect(cards(wrapper)[2]).toContain('202 GB')
    const releasable = wrapper
      .findAll('.cleanup-list li')
      .filter((row) => row.findAll('button').some((button) => button.text() === '放行'))
      .map((row) => row.find('strong').text())
    expect(releasable).toEqual(['蓝色情结', '红宝石戒指'])
    expect(wrapper.text()).toContain('另有 1 项即使放行也不会删除')
    expect(wrapper.text()).toContain('尚未确认入库')
    expect(wrapper.text()).toContain('没有已放行的种子')
  })

  it('releases one download and moves it to the delete list', async () => {
    mocks.cleanupPreview
      .mockResolvedValueOnce(preview([item()]))
      .mockResolvedValueOnce(preview([item({ cleanup_state: 'NONE' })]))
    mocks.setCleanupHold.mockResolvedValue({})
    const wrapper = mountView()
    await flushPromises()

    await wrapper.findAll('button').find((button) => button.text() === '放行')!.trigger('click')
    await flushPromises()

    expect(mocks.setCleanupHold).toHaveBeenCalledWith('d1', false)
    expect(cards(wrapper)[1]).toContain('1 项')
    expect(cards(wrapper)[2]).toContain('0 项')
    // Cleanup is not switched on yet, so nothing is tagged until it is.
    expect(wrapper.text()).toContain('启用后标记')
    expect(wrapper.findAll('button').some((button) => button.text() === '保留')).toBe(true)
  })

  it('warns before a save that starts real deletion, and saves only cleanup fields', async () => {
    mocks.cleanupPreview.mockResolvedValue(preview([item({ cleanup_state: 'NONE' })]))
    mocks.updateCleanupPolicy.mockResolvedValue(
      policy({ cleanup_enabled: true, cleanup_dry_run: false }),
    )
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).not.toContain('保存后将开始真正删除')
    expect(wrapper.text()).not.toContain('有未保存的修改')
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeDefined()
    await wrapper.find('input[name="cleanup_enabled"]').setValue(true)
    await wrapper.find('input[name="cleanup_dry_run"]').setValue(false)
    expect(wrapper.text()).toContain('保存后将开始真正删除')
    // Ticking a box is not saving it.
    expect(wrapper.text()).toContain('有未保存的修改')
    expect(cards(wrapper)[0]).toContain('未启用')

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(mocks.updateCleanupPolicy).toHaveBeenCalledWith({
      cleanup_enabled: true,
      cleanup_dry_run: false,
      cleanup_after_days: 10,
      cleanup_min_seeding_days: 10,
      cleanup_grace_days: 0,
      cleanup_require_library_confirmed: true,
      cleanup_daily_limit: 20,
      cleanup_release_backlog: false,
    })
    expect(cards(wrapper)[0]).toContain('正在运行')
    expect(wrapper.text()).toContain('下一轮标记')
    expect(wrapper.text()).not.toContain('保存后将开始真正删除')
    expect(wrapper.text()).not.toContain('有未保存的修改')

    // The settings come before the lists they govern.
    const headings = wrapper.findAll('h2').map((heading) => heading.text())
    expect(headings).toEqual(['设置', '待删除', '保留中', '最近的清理记录'])
  })

  it('releases several at once, after a second click, and leaves deliberate holds out', async () => {
    mocks.cleanupPreview
      .mockResolvedValueOnce(
        preview([
          item({ hold_reason: 'BACKLOG' }),
          item({ download_id: 'd2', media_title: '红宝石戒指', hold_reason: 'BACKLOG' }),
          item({ download_id: 'kept', media_title: '大物', hold_reason: 'MANUAL' }),
        ]),
      )
      .mockResolvedValueOnce(preview([item({ download_id: 'kept', hold_reason: 'MANUAL' })]))
    mocks.setCleanupHolds.mockResolvedValue({ changed: 2, skipped: [] })
    const wrapper = mountView()
    await flushPromises()

    // The one held on purpose is shown, labelled, and cannot be ticked.
    expect(wrapper.text()).toContain('手动保留')
    expect(wrapper.findAll('.cleanup-list input[type="checkbox"]')).toHaveLength(2)
    expect(wrapper.text()).toContain('全选（2 项）')

    const bulk = () => wrapper.find('.cleanup-bulk-actions')
    expect(bulk().find('button').attributes('disabled')).toBeDefined()
    await wrapper.find('input[name="select_all"]').setValue(true)
    expect(wrapper.text()).toContain('已选 2 项 · 4.0 GB')

    await bulk().find('button').trigger('click')
    expect(mocks.setCleanupHolds).not.toHaveBeenCalled()
    expect(bulk().text()).toContain('确认放行 2 项（4.0 GB）')

    await bulk().findAll('button')[1]!.trigger('click')
    await flushPromises()

    expect(mocks.setCleanupHolds).toHaveBeenCalledWith(['d1', 'd2'], false)
    expect(wrapper.text()).toContain('已放行 2 项')
  })

  it('spells out what switching on automatic release will do before saving', async () => {
    mocks.cleanupPreview.mockResolvedValue(
      preview([
        item({ hold_reason: 'BACKLOG' }),
        item({ download_id: 'kept', hold_reason: 'TAG_REMOVED' }),
      ]),
    )
    mocks.updateCleanupPolicy.mockResolvedValue(policy({ cleanup_release_backlog: true }))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('摘标签保留')
    await wrapper.find('input[name="cleanup_release_backlog"]').setValue(true)
    expect(wrapper.text()).toContain('1 项满足条件的存量（2.0 GB）会在下一轮自动放行')
    expect(wrapper.text()).toContain('每天最多删 20 个')

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(mocks.updateCleanupPolicy).toHaveBeenCalledWith(
      expect.objectContaining({ cleanup_release_backlog: true }),
    )
    expect(wrapper.text()).toContain('「自动放行存量」已开启')
  })

  it('says deletion is not authorised when the server switch is off', async () => {
    mocks.policy.mockResolvedValue(policy({ cleanup_enabled: true, cleanup_dry_run: false }))
    mocks.cleanupPreview.mockResolvedValue(preview([], { delete_authorized: false }))
    const wrapper = mountView()
    await flushPromises()

    expect(cards(wrapper)[0]).toContain('未授权删除')
    expect(wrapper.text()).toContain('（当前未开启）')
  })

  it('shows tagged torrents with when they will be deleted, and recent history', async () => {
    mocks.policy.mockResolvedValue(policy({ cleanup_enabled: true, cleanup_dry_run: false }))
    mocks.cleanupPreview.mockResolvedValue(
      preview([item({ cleanup_state: 'MARKED', deletes_at: '2026-10-05T04:00:00Z' })]),
    )
    mocks.list.mockResolvedValue({
      items: [
        {
          id: 'a1',
          created_at: '2026-10-05T03:40:00Z',
          event: 'DOWNLOAD_CLEANUP_MARKED',
          category: 'cleanup',
          message: 'Blue.Complex.S01.1080p 已标记待清理，0 天后删除',
          actor: 'system:cleanup',
          trigger: 'AUTO',
          reason: null,
          details: {},
          media_id: 'm1',
          media_title: '蓝色情结',
        },
      ],
      total: 1,
      page: 1,
      page_size: 8,
    })
    const wrapper = mountView()
    await flushPromises()

    expect(mocks.list).toHaveBeenCalledWith({ category: 'cleanup', pageSize: 8 })
    expect(wrapper.text()).toContain('已标记 · 2026/10/05 12:00:00 后删除')
    expect(wrapper.text()).toContain('标记待清理')
    expect(wrapper.text()).toContain('空间清理 · 2026/10/05 11:40:00')
  })

  it('keeps the settings usable when qBittorrent cannot be reached', async () => {
    mocks.cleanupPreview.mockRejectedValue(new Error('down'))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('无法读取清理清单')
    expect(wrapper.find('input[name="cleanup_enabled"]').exists()).toBe(true)
  })
})
