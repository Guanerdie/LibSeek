import type {
  MediaImportCreateRequest,
  MediaImportRequest,
  MediaImportRequestSummary,
  MediaImportStatus,
} from '../src/types'

export function makeMediaImportRequest(
  status: MediaImportStatus = 'REVIEW_REQUIRED',
  withPreflight = true,
): MediaImportRequest {
  const hash = 'a'.repeat(64)
  return {
    id: 'import-1',
    download_job_id: 'job-1',
    media_item_id: 'media-1',
    execution_id: 'execution-1',
    status,
    requested_by: 'operator-user',
    requested_at: '2026-08-11T07:00:00Z',
    approved_by: status === 'APPROVED_PLAN_ONLY' ? 'admin-user' : null,
    approved_at: status === 'APPROVED_PLAN_ONLY' ? '2026-08-11T07:06:00Z' : null,
    rejected_by: status === 'REJECTED' ? 'admin-user' : null,
    rejected_at: status === 'REJECTED' ? '2026-08-11T07:06:00Z' : null,
    rejection_reason: status === 'REJECTED' ? 'mapping rejected' : null,
    revoked_by: status === 'REVOKED' ? 'admin-user' : null,
    revoked_at: status === 'REVOKED' ? '2026-08-11T07:08:00Z' : null,
    revocation_reason: status === 'REVOKED' ? 'plan revoked' : null,
    decision_acknowledgements:
      status === 'APPROVED_PLAN_ONLY'
        ? {
            acknowledges_plan_only: true,
            acknowledges_source_retention: true,
            acknowledges_no_overwrite: true,
            acknowledges_hnr: true,
          }
        : null,
    created_at: '2026-08-11T07:00:00Z',
    updated_at: '2026-08-11T07:05:00Z',
    plan: {
      id: 'plan-1',
      request_id: 'import-1',
      download_job_id: 'job-1',
      media_item_id: 'media-1',
      execution_id: 'execution-1',
      mode: 'PLAN_ONLY_NO_FILE_OPERATION',
      proposed_operation: 'HARDLINK',
      source_manifest: {
        source_root_ref: 'downloads-complete',
        files: [
          { relative_path: 'Series/S01E01.mkv', size_bytes: 2_147_483_648 },
          { relative_path: 'Series/S01E01.zh.srt', size_bytes: 12_345 },
          { relative_path: 'Series/sample.txt', size_bytes: 32 },
        ],
      },
      source_manifest_hash: 'b'.repeat(64),
      target_mapping: {
        target_root_ref: 'tv-library',
        files: [
          {
            source_relative_path: 'Series/S01E01.zh.srt',
            target_relative_path: 'Series (2026)/Season 01/Series - S01E01.zh.srt',
          },
          {
            source_relative_path: 'Series/S01E01.mkv',
            target_relative_path: 'Series (2026)/Season 01/Series - S01E01.mkv',
          },
        ],
        source_retention: true,
        overwrite: false,
      },
      target_mapping_hash: 'c'.repeat(64),
      job_summary_snapshot: {
        download_job_id: 'job-1',
        media_item_id: 'media-1',
        execution_id: 'execution-1',
        approval_id: 'approval-1',
        job_status: 'COMPLETED',
        progress: 1,
        info_hash_v1: 'd'.repeat(40),
        info_hash_v2: null,
        size_bytes: 2_147_496_025,
        file_count: 3,
        completed_at: '2026-08-11T06:30:00Z',
        hnr_status: 'UNKNOWN',
        media_type: 'tv',
        tmdb_id: 42,
        media_title: '测试剧集',
        media_year: 2026,
        execution_status: 'SUBMITTED',
        actual_info_hash_v1: 'd'.repeat(40),
        actual_info_hash_v2: null,
        actual_size_bytes: 2_147_496_025,
        actual_file_count: 3,
        verified_at: '2026-08-11T06:01:00Z',
      },
      summary_snapshot_hash: 'e'.repeat(64),
      config_fingerprint: 'f'.repeat(64),
      plan_hash: hash,
      created_by: 'operator-user',
      created_at: '2026-08-11T07:00:00Z',
    },
    preflight: withPreflight
      ? {
          id: 'preflight-1',
          request_id: 'import-1',
          plan_id: 'plan-1',
          overall_status: 'WARNING',
          inspection_hash: '1'.repeat(64),
          result_hash: '2'.repeat(64),
          preflight_hash: '3'.repeat(64),
          config_fingerprint: 'f'.repeat(64),
          result: {
            overall_status: 'WARNING',
            checked_at: '2026-08-11T07:05:00Z',
            config_fingerprint: 'f'.repeat(64),
            checks: [
              { code: 'SOURCE_FILES_COMPLETE', status: 'PASS', message: '源文件完整' },
              { code: 'HNR_NOT_SATISFIED', status: 'WARNING', message: 'H&R 尚未满足' },
            ],
          },
          checked_by: 'media-import-preflight',
          checked_at: '2026-08-11T07:05:00Z',
          created_at: '2026-08-11T07:05:00Z',
        }
      : null,
    events: [
      {
        id: 'event-1',
        request_id: 'import-1',
        event_type: 'MEDIA_IMPORT_PLAN_PROPOSAL_CREATED',
        from_status: null,
        to_status: 'PREFLIGHT_REQUIRED',
        actor: 'operator-user',
        sanitized_details: { execution_authorized: false },
        created_at: '2026-08-11T07:00:00Z',
      },
      {
        id: 'event-2',
        request_id: 'import-1',
        event_type: 'MEDIA_IMPORT_TRUSTED_PREFLIGHT_RECORDED',
        from_status: 'PREFLIGHT_REQUIRED',
        to_status: 'REVIEW_REQUIRED',
        actor: 'media-import-preflight',
        sanitized_details: { read_only: true },
        created_at: '2026-08-11T07:05:00Z',
      },
    ],
  }
}

export function makeMediaImportCreateRequest(): MediaImportCreateRequest {
  return {
    download_job_id: 'job-1',
    proposed_operation: 'HARDLINK',
    source_manifest: {
      source_root_ref: 'downloads-complete',
      files: [{ relative_path: 'Movie/Movie.mkv', size_bytes: 1024 }],
    },
    target_mapping: {
      target_root_ref: 'movie-library',
      files: [
        {
          source_relative_path: 'Movie/Movie.mkv',
          target_relative_path: 'Movie (2026)/Movie (2026).mkv',
        },
      ],
      source_retention: true,
      overwrite: false,
    },
  }
}

export function makeMediaImportSummary(
  request: MediaImportRequest = makeMediaImportRequest(),
): MediaImportRequestSummary {
  const job = request.plan.job_summary_snapshot
  return {
    id: request.id,
    download_job_id: request.download_job_id,
    media_item_id: request.media_item_id,
    execution_id: request.execution_id,
    status: request.status,
    requested_by: request.requested_by,
    requested_at: request.requested_at,
    updated_at: request.updated_at,
    plan_id: request.plan.id,
    mode: request.plan.mode,
    proposed_operation: request.plan.proposed_operation,
    plan_hash: request.plan.plan_hash,
    media_type: job.media_type,
    tmdb_id: job.tmdb_id,
    media_title: job.media_title,
    media_year: job.media_year,
    source_root_ref: request.plan.source_manifest.source_root_ref,
    target_root_ref: request.plan.target_mapping.target_root_ref,
    file_count: job.file_count,
    size_bytes: job.size_bytes,
    preflight_status: request.preflight?.overall_status ?? null,
    preflight_checked_at: request.preflight?.checked_at ?? null,
    preflight_hash: request.preflight?.preflight_hash ?? null,
  }
}
