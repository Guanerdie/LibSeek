import type { MediaItem, TorrentSearchRun } from '../types'

const CONFIRMED_IDENTITY_STATUSES: ReadonlySet<MediaItem['workflow_status']> = new Set([
  'IDENTITY_CONFIRMED',
  'PT_SEARCH_PENDING',
  'PT_SEARCHING',
  'TORRENT_REVIEW',
  'NO_CANDIDATE',
  'SEARCH_FAILED',
])

export function hasConfirmedIdentityStatus(status: MediaItem['workflow_status']): boolean {
  return CONFIRMED_IDENTITY_STATUSES.has(status)
}

export function hasConfirmedIdentityEvidence(
  media: MediaItem | null,
  runs: readonly TorrentSearchRun[],
): boolean {
  return Boolean(media && (hasConfirmedIdentityStatus(media.workflow_status) || runs.length > 0))
}
