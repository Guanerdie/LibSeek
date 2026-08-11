import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '../src/stores/auth'
import MediaImportCreateView from '../src/views/MediaImportCreateView.vue'
import MediaImportDetailView from '../src/views/MediaImportDetailView.vue'
import MediaImportListView from '../src/views/MediaImportListView.vue'
import { makeMediaImportRequest, makeMediaImportSummary } from './media-import-fixture'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  create: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
  revoke: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  authApi: {},
  setApiCsrfToken: vi.fn(),
  mediaImportApi: mocks,
}))

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/media-imports', component: MediaImportListView },
      { path: '/media-imports/new', component: MediaImportCreateView },
      { path: '/media-imports/:id', component: MediaImportDetailView },
      { path: '/download-jobs/:id', component: { template: '<div />' } },
    ],
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  const auth = useAuthStore()
  auth.initialized = true
  auth.principal = { username: 'admin-user', role: 'admin' }
})

describe('media import views', () => {
  it('renders desktop and mobile list records with the plan-only boundary', async () => {
    const item = makeMediaImportSummary()
    mocks.list.mockResolvedValueOnce({ items: [item], page: 1, page_size: 50, total: 1 })
    const router = makeRouter()
    await router.push('/media-imports?download_job_id=job-1')

    const wrapper = mount(MediaImportListView, { global: { plugins: [router] } })
    await flushPromises()

    expect(mocks.list).toHaveBeenCalledWith({
      page: 1,
      pageSize: 50,
      status: undefined,
      downloadJobId: 'job-1',
    })
    expect(wrapper.text()).toContain('仅规划，不操作媒体文件')
    expect(wrapper.text()).toContain('测试剧集')
    expect(wrapper.text()).toContain('警告')
    expect(wrapper.findAll('.media-import-mobile-item')).toHaveLength(1)
    expect(wrapper.find('a[href="/media-imports/import-1"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/media-imports/new?download_job_id=job-1"]').exists()).toBe(true)
  })

  it('reloads the list when the download job query changes on the same route', async () => {
    mocks.list
      .mockResolvedValueOnce({ items: [], page: 1, page_size: 50, total: 0 })
      .mockResolvedValueOnce({ items: [], page: 1, page_size: 50, total: 0 })
    const router = makeRouter()
    await router.push('/media-imports?download_job_id=job-1')
    const wrapper = mount(MediaImportListView, { global: { plugins: [router] } })
    await flushPromises()

    await router.push('/media-imports?download_job_id=job-2')
    await flushPromises()

    expect(mocks.list).toHaveBeenLastCalledWith({
      page: 1,
      pageSize: 50,
      status: undefined,
      downloadJobId: 'job-2',
    })
    expect((wrapper.get('input[aria-label="下载任务 ID 筛选"]').element as HTMLInputElement).value)
      .toBe('job-2')
  })

  it('creates the exact manifest and mapping contract then opens the detail', async () => {
    const created = makeMediaImportRequest('PREFLIGHT_REQUIRED', false)
    mocks.create.mockResolvedValueOnce(created)
    const router = makeRouter()
    await router.push(
      '/media-imports/new?download_job_id=job-1&source_root_ref=downloads-complete',
    )
    const wrapper = mount(MediaImportCreateView, { global: { plugins: [router] } })

    await wrapper.get('input[aria-label="目标根引用"]').setValue('tv-library')
    await wrapper.get('input[aria-label="源相对路径 1"]').setValue('Series/S01E01.mkv')
    await wrapper.get('input[aria-label="文件大小 1"]').setValue('2147483648')
    await wrapper
      .get('input[aria-label="目标相对路径 1"]')
      .setValue('Series (2026)/Season 01/Series - S01E01.mkv')
    await wrapper.get('.file-mapping-section .button').trigger('click')
    await wrapper.get('input[aria-label="源相对路径 2"]').setValue('Series/sample.txt')
    await wrapper.get('input[aria-label="文件大小 2"]').setValue('32')
    await wrapper.get('input[aria-label="加入目标映射 2"]').setValue(false)
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.create).toHaveBeenCalledWith({
      download_job_id: 'job-1',
      proposed_operation: 'HARDLINK',
      source_manifest: {
        source_root_ref: 'downloads-complete',
        files: [
          { relative_path: 'Series/S01E01.mkv', size_bytes: 2_147_483_648 },
          { relative_path: 'Series/sample.txt', size_bytes: 32 },
        ],
      },
      target_mapping: {
        target_root_ref: 'tv-library',
        files: [
          {
            source_relative_path: 'Series/S01E01.mkv',
            target_relative_path: 'Series (2026)/Season 01/Series - S01E01.mkv',
          },
        ],
        source_retention: true,
        overwrite: false,
      },
    })
    expect(router.currentRoute.value.path).toBe('/media-imports/import-1')
  })

  it('requires at least one selected target mapping and presents overwrite=false correctly', async () => {
    const router = makeRouter()
    await router.push(
      '/media-imports/new?download_job_id=job-1&source_root_ref=downloads-complete',
    )
    const wrapper = mount(MediaImportCreateView, { global: { plugins: [router] } })

    await wrapper.get('input[aria-label="目标根引用"]').setValue('tv-library')
    await wrapper.get('input[aria-label="源相对路径 1"]').setValue('Series/sample.txt')
    await wrapper.get('input[aria-label="文件大小 1"]').setValue('32')
    await wrapper.get('input[aria-label="加入目标映射 1"]').setValue(false)
    expect(wrapper.get('input[aria-label="目标相对路径 1"]').attributes('disabled')).toBeDefined()
    await wrapper.get('form').trigger('submit')

    expect(wrapper.text()).toContain('至少选择一个文件加入目标映射')
    expect(wrapper.text()).toContain('允许覆盖目标文件')
    const fixedSettings = wrapper.findAll('.fixed-safety-settings input')
    expect((fixedSettings[0]?.element as HTMLInputElement).checked).toBe(true)
    expect((fixedSettings[1]?.element as HTMLInputElement).checked).toBe(false)
    expect(mocks.create).not.toHaveBeenCalled()
  })

  it('shows trusted preflight evidence and requires all approval confirmations', async () => {
    const item = makeMediaImportRequest()
    mocks.get.mockResolvedValueOnce(item)
    mocks.approve.mockResolvedValueOnce(makeMediaImportRequest('APPROVED_PLAN_ONLY'))
    const router = makeRouter()
    await router.push('/media-imports/import-1')
    const wrapper = mount(MediaImportDetailView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('仅规划，不操作媒体文件')
    expect(wrapper.text()).toContain('SOURCE_FILES_COMPLETE')
    expect(wrapper.text()).toContain('HNR_NOT_SATISFIED')
    expect(wrapper.text()).toContain('MEDIA_IMPORT_TRUSTED_PREFLIGHT_RECORDED')
    const mappingRows = wrapper.findAll('.media-import-mapping-table tbody tr')
    expect(mappingRows[0]?.text()).toContain('Series (2026)/Season 01/Series - S01E01.mkv')
    expect(mappingRows[1]?.text()).toContain('Series (2026)/Season 01/Series - S01E01.zh.srt')
    expect(mappingRows[2]?.text()).toContain('未加入目标映射')
    const approveButton = wrapper.findAll('button').find((button) => button.text() === '批准纯规划')
    expect(approveButton?.attributes('disabled')).toBeDefined()

    const checks = wrapper.findAll('.plan-acknowledgements input')
    expect(checks).toHaveLength(4)
    for (const checkbox of checks) await checkbox.setValue(true)
    expect(approveButton?.attributes('disabled')).toBeUndefined()
    await approveButton?.trigger('click')
    await flushPromises()

    expect(mocks.approve).toHaveBeenCalledWith('import-1', {
      acknowledges_plan_only: true,
      acknowledges_source_retention: true,
      acknowledges_no_overwrite: true,
      acknowledges_hnr: true,
    })
    expect(wrapper.text()).toContain('撤销规划')
    const commandLabels = wrapper.findAll('button').map((button) => button.text())
    expect(commandLabels).not.toContain('执行入库')
    expect(commandLabels).not.toContain('扫描目录')
    expect(commandLabels).not.toContain('移动文件')
    expect(commandLabels).not.toContain('删除文件')
  })

  it('keeps viewer decisions read-only while retaining plan evidence', async () => {
    const auth = useAuthStore()
    auth.principal = { username: 'viewer-user', role: 'viewer' }
    mocks.get.mockResolvedValueOnce(makeMediaImportRequest())
    const router = makeRouter()
    await router.push('/media-imports/import-1')
    const wrapper = mount(MediaImportDetailView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('当前角色只读')
    for (const checkbox of wrapper.findAll('.decision-section input[type="checkbox"]')) {
      expect(checkbox.attributes('disabled')).toBeDefined()
    }
    expect(mocks.approve).not.toHaveBeenCalled()
  })
})
