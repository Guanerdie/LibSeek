import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useDownloadJobStore } from '../src/stores/downloadJobs'
import type { DownloadJob, DownloadJobSummary, DownloadJobTimeline } from '../src/types'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  summary: vi.fn(),
  timeline: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  downloadJobApi: mocks,
}))

function makeJob(id: string): DownloadJob {
  return {
    id,
    execution_id: `execution-${id}`,
    approval_id: `approval-${id}`,
    media_item_id: `media-${id}`,
    status: 'DOWNLOADING',
    release_title: `Test Series ${id} S01E03 1080p WEB-DL`,
    info_hash_v1: 'a'.repeat(40),
    info_hash_v2: null,
    save_path_ref: 'media-library',
    category: 'media',
    size_bytes: 4_294_967_296,
    file_count: 1,
    progress: 0.5,
    download_speed_bps: 2_097_152,
    upload_speed_bps: 262_144,
    downloaded_bytes: 2_147_483_648,
    uploaded_bytes: 268_435_456,
    ratio: 0.12,
    hnr_status: 'UNKNOWN',
    started_at: '2026-08-11T01:00:00Z',
    completed_at: null,
    last_seen_at: '2026-08-11T01:05:00Z',
    error_code: null,
    error_message: null,
    created_at: '2026-08-11T01:00:00Z',
    updated_at: '2026-08-11T01:05:00Z',
  }
}

function makeSummary(job: DownloadJob): DownloadJobSummary {
  return {
    job,
    media: {
      id: job.media_item_id,
      title: '测试剧集',
      media_type: 'tv',
      tmdb_id: 42,
      year: 2026,
      season: 1,
      episodes: [3],
    },
    approval: {
      id: job.approval_id,
      status: 'CONSUMED',
      snapshot_hash: 'b'.repeat(64),
      expires_at: '2026-08-11T02:00:00Z',
    },
    execution: {
      id: job.execution_id,
      status: 'SUBMITTED',
      launch_mode: 'ADD_PAUSED',
      requires_reconciliation: false,
      actual_info_hash_v1: 'a'.repeat(40),
      actual_info_hash_v2: null,
      validated_at: '2026-08-11T00:59:00Z',
      submitted_at: '2026-08-11T01:00:00Z',
      verified_at: '2026-08-11T01:00:02Z',
    },
    warnings: ['HNR_UNKNOWN'],
  }
}

function makeTimeline(job: DownloadJob): DownloadJobTimeline {
  return {
    job_id: job.id,
    items: [
      {
        source: 'job',
        event_type: 'JOB_PROGRESS_UPDATED',
        from_status: 'QUEUED',
        to_status: 'DOWNLOADING',
        actor: 'download-monitor',
        sanitized_details: { progress: 0.5 },
        created_at: '2026-08-11T01:05:00Z',
      },
    ],
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('download job store', () => {
  it('consumes the stable paginated list contract', async () => {
    const job = makeJob('job-1')
    mocks.list.mockResolvedValueOnce({ items: [job], page: 2, page_size: 50, total: 53 })
    const store = useDownloadJobStore()

    await store.loadList('DOWNLOADING', 2)

    expect(mocks.list).toHaveBeenCalledWith({ page: 2, pageSize: 50, status: 'DOWNLOADING' })
    expect(store.jobs).toEqual([job])
    expect(store.page).toBe(2)
    expect(store.pageSize).toBe(50)
    expect(store.total).toBe(53)
  })

  it('loads job, nested summary and sanitized timeline as one detail snapshot', async () => {
    const job = makeJob('job-1')
    mocks.get.mockResolvedValueOnce(job)
    mocks.summary.mockResolvedValueOnce(makeSummary(job))
    mocks.timeline.mockResolvedValueOnce(makeTimeline(job))
    const store = useDownloadJobStore()

    await store.loadDetail('job-1')

    expect(mocks.get).toHaveBeenCalledWith('job-1')
    expect(mocks.summary).toHaveBeenCalledWith('job-1')
    expect(mocks.timeline).toHaveBeenCalledWith('job-1')
    expect(store.selected?.id).toBe('job-1')
    expect(store.summary?.media.title).toBe('测试剧集')
    expect(store.timeline[0]?.sanitized_details).toEqual({ progress: 0.5 })
  })

  it('ignores a stale detail response after navigation to another job', async () => {
    const firstJob = makeJob('job-1')
    const secondJob = makeJob('job-2')
    let releaseFirst: ((value: DownloadJob) => void) | undefined
    const firstResponse = new Promise<DownloadJob>((resolve) => {
      releaseFirst = resolve
    })
    mocks.get.mockImplementation((jobId: string) =>
      jobId === 'job-1' ? firstResponse : Promise.resolve(secondJob),
    )
    mocks.summary.mockImplementation((jobId: string) => {
      const job = jobId === 'job-1' ? firstJob : secondJob
      return Promise.resolve(makeSummary(job))
    })
    mocks.timeline.mockImplementation((jobId: string) => {
      const job = jobId === 'job-1' ? firstJob : secondJob
      return Promise.resolve(makeTimeline(job))
    })
    const store = useDownloadJobStore()

    const oldRequest = store.loadDetail('job-1')
    await store.loadDetail('job-2')
    releaseFirst?.(firstJob)
    await oldRequest

    expect(store.selected?.id).toBe('job-2')
    expect(store.summary?.job.id).toBe('job-2')
    expect(store.detailError).toBeNull()
  })

  it('rejects inconsistent endpoint identities without showing mixed data', async () => {
    const job = makeJob('job-1')
    const wrongJob = makeJob('job-other')
    mocks.get.mockResolvedValueOnce(job)
    mocks.summary.mockResolvedValueOnce(makeSummary(wrongJob))
    mocks.timeline.mockResolvedValueOnce(makeTimeline(job))
    const store = useDownloadJobStore()

    await store.loadDetail('job-1')

    expect(store.selected).toBeNull()
    expect(store.summary).toBeNull()
    expect(store.timeline).toEqual([])
    expect(store.detailError).toContain('响应不一致')
  })
})
