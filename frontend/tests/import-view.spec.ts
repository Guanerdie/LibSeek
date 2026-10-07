import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ImportBatch, ImportMatchLine, ImportMediaChoice } from '../src/types'
import ImportView from '../src/views/ImportView.vue'

const mocks = vi.hoisted(() => ({
  importMatch: vi.fn(),
  importStart: vi.fn(),
  importStatus: vi.fn(),
  importCancel: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  dailyApi: mocks,
}))

function media(id: string, title: string, overrides: Partial<ImportMediaChoice> = {}): ImportMediaChoice {
  return {
    media_id: id,
    title,
    original_title: null,
    year: 2020,
    media_type: 'tv',
    tmdb_id: 1,
    ...overrides,
  }
}

function line(raw: string, overrides: Partial<ImportMatchLine> = {}): ImportMatchLine {
  return { raw, status: 'MATCHED', media: null, choices: [], note: null, ...overrides }
}

function batch(overrides: Partial<ImportBatch> = {}): ImportBatch {
  return {
    id: '20261007-100000',
    state: 'IDLE',
    started_by: null,
    started_at: null,
    finished_at: null,
    cancel_requested: false,
    items: [],
    ...overrides,
  }
}

function mountView() {
  return mount(ImportView, {
    global: {
      stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } },
    },
  })
}

async function matchWith(wrapper: ReturnType<typeof mountView>, lines: ImportMatchLine[]) {
  mocks.importMatch.mockResolvedValueOnce({ lines, truncated: false })
  await wrapper.find('textarea').setValue('红宝石戒指\n同名\n不存在')
  await wrapper.find('form').trigger('submit')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.importStatus.mockResolvedValue(batch())
})

afterEach(() => {
  vi.useRealTimers()
})

describe('bulk import page', () => {
  it('shows what each line matched and downloads only what is settled', async () => {
    const wrapper = mountView()
    await flushPromises()
    await matchWith(wrapper, [
      line('红宝石戒指', { media: media('m1', '红宝石戒指', { year: 2013 }) }),
      line('同名', {
        status: 'AMBIGUOUS',
        choices: [media('m2', '同名', { media_type: 'movie', year: 2001 }), media('m3', '同名')],
      }),
      line('不存在', { status: 'NOT_FOUND' }),
      line('已入库的剧', {
        status: 'NOT_MISSING',
        media: media('m4', '已入库的剧'),
        note: 'NextFind 没有把它列为缺失，可能已经入库',
      }),
    ])

    expect(mocks.importMatch).toHaveBeenCalledWith('红宝石戒指\n同名\n不存在')
    expect(wrapper.text()).toContain('1 部将下载，1 部需要你选择，1 部库中已有、未勾选，1 部不会下载')
    expect(wrapper.text()).toContain('红宝石戒指（2013 · 电视剧）')
    // In the library already: offered, but not downloaded unless ticked.
    expect(wrapper.text()).toContain('仍然下载（不会被自动清理）')
    const start = () => wrapper.find('.import-start button')
    expect(start().text()).toBe('开始下载 1 部')

    // Nothing is picked for an unclear line until the operator picks it.
    await wrapper.find('select').setValue('m2')
    expect(start().text()).toBe('开始下载 2 部')
    expect(wrapper.text()).not.toContain('需要你选择，')

    mocks.importStart.mockResolvedValueOnce(
      batch({
        state: 'RUNNING',
        started_by: 'owner',
        started_at: '2026-10-07T02:00:00Z',
        items: [
          { media_id: 'm1', title: '红宝石戒指', outcome: 'RUNNING', message: null, selected_title: null, download_id: null },
          { media_id: 'm2', title: '同名', outcome: 'PENDING', message: null, selected_title: null, download_id: null },
        ],
      }),
    )
    await start().trigger('click')
    await flushPromises()

    expect(mocks.importStart).toHaveBeenCalledWith(['m1', 'm2'], [])
    expect(wrapper.text()).toContain('正在下载这一批')
    expect(wrapper.text()).toContain('已处理 0 / 2 部')
    expect(wrapper.text()).toContain('搜索中…')
    wrapper.unmount()
  })

  it('downloads a title already in the library only when its box is ticked', async () => {
    const wrapper = mountView()
    await flushPromises()
    await matchWith(wrapper, [
      line('红宝石戒指', { media: media('m1', '红宝石戒指') }),
      line('已入库的剧', {
        status: 'NOT_MISSING',
        media: media('m4', '已入库的剧', { in_library: true }),
      }),
      line('另一部已有', {
        status: 'NOT_MISSING',
        media: media('m5', '另一部已有', { in_library: true }),
      }),
    ])
    const start = () => wrapper.find('.import-start button')

    expect(start().text()).toBe('开始下载 1 部')
    expect(wrapper.text()).toContain('库中已有的 2 部也下载')

    // One line at a time...
    await wrapper.find('input[aria-label="仍然下载「已入库的剧」"]').setValue(true)
    expect(start().text()).toBe('开始下载 2 部')
    expect(wrapper.text()).toContain('2 部将下载（其中 1 部库中已有）')
    expect(wrapper.text()).toContain('1 部库中已有、未勾选')

    // ...or all of them.
    await wrapper.find('input[name="again_all"]').setValue(true)
    expect(start().text()).toBe('开始下载 3 部')

    mocks.importStart.mockResolvedValueOnce(batch({ state: 'RUNNING', started_by: 'owner' }))
    await start().trigger('click')
    await flushPromises()

    // The server is told which ones were asked for on purpose.
    expect(mocks.importStart).toHaveBeenCalledWith(['m1', 'm4', 'm5'], ['m4', 'm5'])
    wrapper.unmount()
  })

  it('follows a running batch and reports each title when it ends', async () => {
    vi.useFakeTimers()
    const item = (overrides: Record<string, unknown>) => ({
      media_id: 'm1',
      title: '红宝石戒指',
      outcome: 'RUNNING',
      message: null,
      selected_title: null,
      download_id: null,
      ...overrides,
    })
    mocks.importStatus
      .mockResolvedValueOnce(batch({ state: 'RUNNING', started_by: 'owner', items: [item({})] as never }))
      .mockResolvedValueOnce(
        batch({
          state: 'FINISHED',
          started_by: 'owner',
          items: [
            item({ outcome: 'DOWNLOADED', selected_title: 'Ruby.Ring.S01.1080p' }),
            item({
              media_id: 'm2',
              title: '少年星球',
              outcome: 'NO_CANDIDATE',
              message: '搜到 3 个资源，都不符合选种标准：做种数不足（3 个）',
            }),
          ] as never,
        }),
      )
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('正在下载这一批')
    await vi.advanceTimersByTimeAsync(2_000)
    await flushPromises()

    expect(wrapper.text()).toContain('上一批的结果')
    expect(wrapper.text()).toContain('已提交下载 1 部')
    expect(wrapper.text()).toContain('没有合格资源 1 部')
    expect(wrapper.text()).toContain('Ruby.Ring.S01.1080p')
    expect(wrapper.text()).toContain('做种数不足（3 个）')
    // Finished: no further polling.
    await vi.advanceTimersByTimeAsync(10_000)
    expect(mocks.importStatus).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('will not start a second batch while one is running, and can stop it', async () => {
    mocks.importStatus.mockResolvedValue(
      batch({
        state: 'RUNNING',
        started_by: 'owner',
        items: [
          { media_id: 'm9', title: '进行中', outcome: 'RUNNING', message: null, selected_title: null, download_id: null },
        ],
      }),
    )
    mocks.importCancel.mockResolvedValue(batch({ state: 'RUNNING', cancel_requested: true }))
    const wrapper = mountView()
    await flushPromises()
    await matchWith(wrapper, [line('红宝石戒指', { media: media('m1', '红宝石戒指') })])

    expect(wrapper.find('.import-start button').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('上一批还在下载')

    await wrapper.findAll('button').find((button) => button.text() === '停止')!.trigger('click')
    await flushPromises()

    expect(mocks.importCancel).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('处理完当前这部后停止')
    wrapper.unmount()
  })
})
