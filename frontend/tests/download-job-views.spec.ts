import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DownloadJobDetailView from '../src/views/DownloadJobDetailView.vue'
import DownloadJobListView from '../src/views/DownloadJobListView.vue'
import { useAuthStore } from '../src/stores/auth'
import type { DownloadJob, DownloadJobSummary, DownloadJobTimeline } from '../src/types'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  summary: vi.fn(),
  timeline: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  authApi: {},
  setApiCsrfToken: vi.fn(),
  downloadJobApi: mocks,
}))

const job: DownloadJob = {
  id: 'job-1',
  execution_id: 'execution-1',
  approval_id: 'approval-1',
  media_item_id: 'media-1',
  status: 'COMPLETED',
  release_title: 'Test Series 2026 S01E03 1080p WEB-DL',
  info_hash_v1: 'a'.repeat(40),
  info_hash_v2: null,
  save_path_ref: 'media-library',
  category: 'media',
  size_bytes: 4_294_967_296,
  file_count: 1,
  progress: 1,
  download_speed_bps: 0,
  upload_speed_bps: 1_048_576,
  downloaded_bytes: 4_294_967_296,
  uploaded_bytes: 8_589_934_592,
  ratio: 2,
  hnr_status: 'UNKNOWN',
  started_at: '2026-08-11T01:00:00Z',
  completed_at: '2026-08-11T01:30:00Z',
  last_seen_at: '2026-08-11T02:00:00Z',
  error_code: null,
  error_message: null,
  created_at: '2026-08-11T01:00:00Z',
  updated_at: '2026-08-11T02:00:00Z',
}

const summary: DownloadJobSummary = {
  job,
  media: {
    id: 'media-1',
    title: '测试剧集',
    media_type: 'tv',
    tmdb_id: 42,
    year: 2026,
    season: 1,
    episodes: [3],
  },
  approval: {
    id: 'approval-1',
    status: 'CONSUMED',
    snapshot_hash: 'b'.repeat(64),
    expires_at: '2026-08-11T02:00:00Z',
  },
  execution: {
    id: 'execution-1',
    status: 'SUBMITTED',
    launch_mode: 'ADD_PAUSED',
    requires_reconciliation: false,
    actual_info_hash_v1: 'a'.repeat(40),
    actual_info_hash_v2: null,
    validated_at: '2026-08-11T00:59:00Z',
    submitted_at: '2026-08-11T01:00:00Z',
    verified_at: '2026-08-11T01:00:02Z',
  },
  warnings: ['HNR_UNKNOWN', 'TRACKER_POLICY_NOT_VERIFIED'],
}

const timeline: DownloadJobTimeline = {
  job_id: 'job-1',
  items: [
    {
      source: 'approval',
      event_type: 'APPROVAL_CONSUMED',
      from_status: 'EXECUTING',
      to_status: 'CONSUMED',
      actor: 'download-executor',
      sanitized_details: {},
      created_at: '2026-08-11T01:00:02Z',
    },
    {
      source: 'execution',
      event_type: 'EXECUTION_SUBMITTED',
      from_status: 'SUBMITTING',
      to_status: 'SUBMITTED',
      actor: 'download-executor',
      sanitized_details: { verified: true },
      created_at: '2026-08-11T01:00:02Z',
    },
    {
      source: 'job',
      event_type: 'JOB_COMPLETED',
      from_status: 'DOWNLOADING',
      to_status: 'COMPLETED',
      actor: 'download-monitor',
      sanitized_details: { progress: 1 },
      created_at: '2026-08-11T01:30:00Z',
    },
  ],
}

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/download-jobs', component: DownloadJobListView },
      { path: '/download-jobs/:id', component: DownloadJobDetailView },
      { path: '/approvals/:id', component: { template: '<div />' } },
      { path: '/executions/:id', component: { template: '<div />' } },
      { path: '/media-imports', component: { template: '<div />' } },
      { path: '/media-imports/new', component: { template: '<div />' } },
    ],
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  const auth = useAuthStore()
  auth.initialized = true
  auth.principal = { username: 'operator-user', role: 'operator' }
})

describe('download job views', () => {
  it('renders a read-only stable page and requests pagination explicitly', async () => {
    mocks.list
      .mockResolvedValueOnce({ items: [job], page: 1, page_size: 50, total: 51 })
      .mockResolvedValueOnce({ items: [], page: 2, page_size: 50, total: 51 })
    const router = makeRouter()
    await router.push('/download-jobs')
    const wrapper = mount(DownloadJobListView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('Test Series 2026 S01E03')
    expect(wrapper.text()).toContain('100.0%')
    expect(wrapper.text()).toContain('H&R UNKNOWN')
    expect(wrapper.text()).not.toContain('H&R SATISFIED')
    expect(wrapper.text()).toContain('不提供暂停、恢复、删除、重校验')
    expect(wrapper.text()).toContain('共 51 条')
    expect(wrapper.find('button[aria-label="暂停"]').exists()).toBe(false)

    await wrapper.get('.pagination button:last-child').trigger('click')
    await flushPromises()
    expect(mocks.list).toHaveBeenLastCalledWith({ page: 2, pageSize: 50, status: undefined })
  })

  it('shows nested summary, warnings and all three sanitized timeline sources', async () => {
    mocks.get.mockResolvedValueOnce(job)
    mocks.summary.mockResolvedValueOnce(summary)
    mocks.timeline.mockResolvedValueOnce(timeline)
    const router = makeRouter()
    await router.push('/download-jobs/job-1')
    const wrapper = mount(DownloadJobDetailView, { global: { plugins: [router] } })
    await flushPromises()

    expect(mocks.get).toHaveBeenCalledWith('job-1')
    expect(mocks.summary).toHaveBeenCalledWith('job-1')
    expect(mocks.timeline).toHaveBeenCalledWith('job-1')
    expect(wrapper.text()).toContain('测试剧集')
    expect(wrapper.text()).toContain('S01 E03')
    expect(wrapper.text()).toContain('H&R 状态为 UNKNOWN')
    expect(wrapper.text()).toContain('不能根据下载完成、做种时间或 Ratio 推断')
    expect(wrapper.text()).not.toContain('义务已经满足。已满足')
    expect(wrapper.text()).toContain('HNR_UNKNOWN')
    expect(wrapper.text()).toContain('TRACKER_POLICY_NOT_VERIFIED')
    expect(wrapper.text()).toContain('审批')
    expect(wrapper.text()).toContain('执行')
    expect(wrapper.text()).toContain('任务')
    expect(wrapper.text()).toContain('APPROVAL_CONSUMED')
    expect(wrapper.text()).toContain('EXECUTION_SUBMITTED')
    expect(wrapper.text()).toContain('JOB_COMPLETED')
    expect(wrapper.text()).toContain('"verified": true')
    expect(wrapper.find('a[href="/approvals/approval-1"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/executions/execution-1"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/media-imports?download_job_id=job-1"]').exists()).toBe(true)
    expect(
      wrapper.find(
        'a[href="/media-imports/new?download_job_id=job-1&source_root_ref=media-library"]',
      ).exists(),
    ).toBe(true)
    expect(wrapper.text()).toContain('仅规划，不操作媒体文件')
    expect(wrapper.findAll('button').map((button) => button.text())).toEqual(['刷新总结'])
  })

  it('renders loading failure without a stale task summary', async () => {
    mocks.get.mockRejectedValueOnce(new Error('下载任务不存在'))
    mocks.summary.mockResolvedValueOnce(summary)
    mocks.timeline.mockResolvedValueOnce(timeline)
    const router = makeRouter()
    await router.push('/download-jobs/missing-job')
    const wrapper = mount(DownloadJobDetailView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('下载任务不存在')
    expect(wrapper.text()).not.toContain('Test Series 2026 S01E03')
    expect(wrapper.find('.job-detail-grid').exists()).toBe(false)
  })
})
