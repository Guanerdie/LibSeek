<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useDownloadJobStore } from '../stores/downloadJobs'
import type { DownloadJobStatus, HnrStatus } from '../types'
import { formatShanghai } from '../utils/format'

const store = useDownloadJobStore()
const status = ref<DownloadJobStatus | ''>('')
const statusOptions: DownloadJobStatus[] = [
  'QUEUED',
  'DOWNLOADING',
  'PAUSED',
  'CHECKING',
  'SEEDING',
  'COMPLETED',
  'MISSING',
  'ERROR',
]

onMounted(() => {
  void store.loadList()
})

function load(page = 1): void {
  void store.loadList(status.value || undefined, page)
}

function formatBytes(value: number): string {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`
}

function formatRate(value: number): string {
  return `${formatBytes(value)}/s`
}

function formatProgress(value: number): string {
  return `${(Math.min(1, Math.max(0, value)) * 100).toFixed(1)}%`
}

function formatRatio(value: number): string {
  return value < 0 ? '未知' : value.toFixed(2)
}

function hnrLabel(value: HnrStatus): string {
  if (value === 'UNKNOWN') return 'UNKNOWN · 规则未知'
  if (value === 'AT_RISK') return 'AT_RISK · 有风险'
  return 'SATISFIED · 后端记录'
}
</script>

<template>
  <section class="page wide-page">
    <PageHeader
      eyebrow="DOWNLOAD JOBS"
      title="下载任务"
      description="只读查看 qBittorrent 中已经核验的下载任务、进度与站点风险信息。"
    >
      <button class="button secondary" :disabled="store.listLoading" @click="load(store.page)">刷新列表</button>
    </PageHeader>

    <div class="phase-banner"><span>只读监控</span>viewer 可查看；本页面不提供暂停、恢复、删除、重校验或文件操作。</div>
    <div class="filter-bar job-filter">
      <select v-model="status" aria-label="下载任务状态" @change="load(1)">
        <option value="">全部状态</option>
        <option v-for="item in statusOptions" :key="item" :value="item">{{ item }}</option>
      </select>
    </div>

    <PageState
      :loading="store.listLoading"
      :error="store.listError"
      :empty="!store.listLoading && !store.listError && store.jobs.length === 0 && store.total === 0"
      empty-text="暂无下载任务"
    />
    <div
      v-if="!store.listLoading && !store.listError && store.jobs.length === 0 && store.total > 0"
      class="notice-state warning-state"
    >
      当前页暂无记录，可返回上一页或刷新筛选结果。
    </div>

    <div v-if="!store.listLoading && !store.listError && store.jobs.length" class="candidate-table-wrap">
      <table class="job-table">
        <thead>
          <tr><th>任务 / 发布名</th><th>状态 / 进度</th><th>实时速度</th><th>分享 / H&amp;R</th><th>大小 / 文件</th><th>最近观测</th><th>错误</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="job in store.jobs" :key="job.id">
            <td class="job-release-cell"><strong>{{ job.release_title }}</strong><small class="mono">{{ job.id }}</small></td>
            <td class="job-progress-cell">
              <StatusPill :status="job.status" />
              <div class="progress-track job-progress-track" aria-hidden="true"><span :style="{ width: formatProgress(job.progress) }"></span></div>
              <small>{{ formatProgress(job.progress) }} · {{ formatBytes(job.downloaded_bytes) }} / {{ formatBytes(job.size_bytes) }}</small>
            </td>
            <td><strong>↓ {{ formatRate(job.download_speed_bps) }}</strong><small>↑ {{ formatRate(job.upload_speed_bps) }}</small></td>
            <td><strong>Ratio {{ formatRatio(job.ratio) }}</strong><small :class="job.hnr_status === 'SATISFIED' ? '' : 'risk-text'">H&amp;R {{ hnrLabel(job.hnr_status) }}</small></td>
            <td><strong>{{ formatBytes(job.size_bytes) }}</strong><small>{{ job.file_count }} 个文件</small></td>
            <td><strong>{{ formatShanghai(job.last_seen_at) }}</strong><small>更新 {{ formatShanghai(job.updated_at) }}</small></td>
            <td><strong :class="job.error_code ? 'error-text' : ''">{{ job.error_code || '—' }}</strong><small>{{ job.error_message || '无' }}</small></td>
            <td><RouterLink class="table-action" :to="`/download-jobs/${job.id}`">查看总结</RouterLink></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div
      v-if="!store.listLoading && !store.listError && (store.total > 0 || store.page > 1)"
      class="pagination standalone-pagination"
    >
      <span>共 {{ store.total }} 条 · 第 {{ store.page }} 页</span>
      <div>
        <button class="button small" :disabled="store.page <= 1" @click="load(store.page - 1)">上一页</button>
        <button class="button small" :disabled="store.page * store.pageSize >= store.total" @click="load(store.page + 1)">下一页</button>
      </div>
    </div>
  </section>
</template>
