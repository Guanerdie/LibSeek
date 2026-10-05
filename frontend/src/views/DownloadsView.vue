<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import Pagination from '../components/Pagination.vue'
import StatusPill from '../components/StatusPill.vue'
import { useDailyStore } from '../stores/daily'
import type { DailyDownload } from '../types'
import { formatShanghai, statusLabel } from '../utils/format'

const daily = useDailyStore()
let refreshTimer: ReturnType<typeof globalThis.setInterval> | undefined
const retryingDownloadId = ref<string | null>(null)
const failedRetryId = ref<string | null>(null)
const holdingDownloadId = ref<string | null>(null)

function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}

function speed(value: number): string {
  if (!value) return '0 MB/s'
  return `${(value / 1024 / 1024).toFixed(1)} MB/s`
}

async function retry(download: DailyDownload): Promise<void> {
  if (retryingDownloadId.value) return
  failedRetryId.value = null
  retryingDownloadId.value = download.id
  try {
    if (!(await daily.retryDownload(download))) failedRetryId.value = download.id
  } finally {
    retryingDownloadId.value = null
  }
}

function finished(download: DailyDownload): boolean {
  return download.state === 'COMPLETED' || download.state === 'SEEDING'
}

/** Where this download stands with space reclaim, in one line. */
function cleanupNote(download: DailyDownload): string | null {
  switch (download.cleanup_state) {
    case 'HELD':
      return '空间清理：保留，不会被自动清理'
    case 'MARKED':
      return `空间清理：已标记待清理（${formatShanghai(download.cleanup_marked_at)}），在 qBittorrent 中摘掉 unin-cleanup 标签可保留`
    case 'DELETED':
      return `空间清理：种子和文件已删除（${formatShanghai(download.cleanup_deleted_at)}）`
    case 'VANISHED':
      return '空间清理：种子已不在 qBittorrent 中'
    default:
      return finished(download) ? '空间清理：符合条件后会被自动清理' : null
  }
}

function canToggleHold(download: DailyDownload): boolean {
  return finished(download) && (download.cleanup_state === 'HELD' || download.cleanup_state === 'NONE')
}

async function toggleHold(download: DailyDownload): Promise<void> {
  if (holdingDownloadId.value) return
  holdingDownloadId.value = download.id
  try {
    await daily.setCleanupHold(download, download.cleanup_state !== 'HELD')
  } finally {
    holdingDownloadId.value = null
  }
}

function changePage(page: number): void {
  void daily.loadDownloads(page)
}

onMounted(() => {
  void daily.refreshDownloads()
  refreshTimer = globalThis.setInterval(() => {
    if (!daily.downloadsSyncing && !retryingDownloadId.value && !holdingDownloadId.value) {
      void daily.refreshDownloads()
    }
  }, 15_000)
})

onBeforeUnmount(() => {
  if (refreshTimer) globalThis.clearInterval(refreshTimer)
})
</script>

<template>
  <section class="page">
    <PageHeader eyebrow="DOWNLOADS" title="下载" description="查看 qBittorrent 任务的当前状态。">
      <button class="button secondary" :disabled="daily.downloadsSyncing" @click="daily.refreshDownloads(daily.downloadsPage)">
        刷新
      </button>
    </PageHeader>

    <PageState
      :loading="daily.downloadsLoading || daily.downloadsSyncing"
      :error="daily.downloadsError"
      :empty="!daily.downloadsLoading && !daily.downloadsSyncing && !daily.downloadsError && daily.downloads.length === 0"
      empty-text="暂无下载任务"
    />

    <div v-if="daily.downloads.length" class="download-stack">
      <article v-for="download in daily.downloads" :key="download.id" class="panel download-card">
        <div class="download-heading">
          <div>
            <h2>{{ download.name }}</h2>
            <p>{{ percent(download.progress) }} · {{ speed(download.download_speed) }} · Ratio {{ download.ratio.toFixed(2) }}</p>
          </div>
          <StatusPill :status="download.state" :label="statusLabel(download.state)" />
        </div>
        <div class="progress-track" :aria-label="`下载进度 ${percent(download.progress)}`">
          <span :style="{ width: percent(download.progress) }"></span>
        </div>
        <p v-if="cleanupNote(download)" class="muted download-cleanup-note">
          {{ cleanupNote(download) }}
          <button
            v-if="canToggleHold(download)"
            class="button secondary small"
            type="button"
            :disabled="holdingDownloadId !== null"
            @click="toggleHold(download)"
          >
            {{ download.cleanup_state === 'HELD' ? '允许自动清理' : '保留不清理' }}
          </button>
        </p>
        <p v-if="download.error_message" class="inline-warning">{{ download.error_message }}</p>
        <p
          v-if="failedRetryId === download.id && daily.downloadsError"
          class="inline-error"
          role="alert"
        >
          {{ daily.downloadsError }}
        </p>
        <button
          v-if="download.state === 'ERROR'"
          class="button primary"
          :disabled="retryingDownloadId !== null"
          @click="retry(download)"
        >
          {{ retryingDownloadId === download.id ? '重试中…' : '重试' }}
        </button>
      </article>
    </div>
    <Pagination
      :page="daily.downloadsPage"
      :total="daily.downloadsTotal"
      :page-size="daily.downloadsPageSize"
      label="下载任务分页"
      @change="changePage"
    />
  </section>
</template>
