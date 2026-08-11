<script setup lang="ts">
import { computed, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useAuthStore } from '../stores/auth'
import { useDownloadJobStore } from '../stores/downloadJobs'
import type { DownloadJobTimelineItem, HnrStatus } from '../types'
import { formatShanghai } from '../utils/format'

const route = useRoute()
const auth = useAuthStore()
const store = useDownloadJobStore()
const job = computed(() => store.summary?.job ?? store.selected)
const sourceLabels: Record<DownloadJobTimelineItem['source'], string> = {
  approval: '审批',
  execution: '执行',
  job: '任务',
}

watch(
  () => String(route.params.id),
  (jobId) => {
    void store.loadDetail(jobId)
  },
  { immediate: true },
)

function refresh(): void {
  void store.loadDetail(String(route.params.id))
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
  if (value === 'UNKNOWN') return 'UNKNOWN（规则未知）'
  if (value === 'AT_RISK') return 'AT_RISK（有风险）'
  return 'SATISFIED（来自后端记录）'
}

function episodeLabel(season: number | null, episodes: number[] | null): string {
  if (season === null && !episodes?.length) return '—'
  const seasonLabel = season === null ? '' : `S${String(season).padStart(2, '0')}`
  const episodeList = episodes?.length
    ? episodes.map((episode) => `E${String(episode).padStart(2, '0')}`).join('、')
    : '整季或未指定'
  return `${seasonLabel} ${episodeList}`.trim()
}

function transitionLabel(item: DownloadJobTimelineItem): string {
  return item.from_status ? `${item.from_status} → ${item.to_status}` : item.to_status
}

function detailsLabel(details: Record<string, unknown>): string {
  return Object.keys(details).length ? JSON.stringify(details, null, 2) : ''
}
</script>

<template>
  <section class="page wide-page">
    <PageHeader
      eyebrow="DOWNLOAD JOB SUMMARY"
      title="下载任务总结"
      description="汇总媒体、审批、执行与 qB 只读监控记录；所有状态均以服务端记录为准。"
    >
      <div class="header-actions">
        <RouterLink class="button secondary" to="/download-jobs">返回任务列表</RouterLink>
        <button class="button secondary" :disabled="store.detailLoading" @click="refresh">刷新总结</button>
      </div>
    </PageHeader>

    <div class="phase-banner"><span>viewer</span>此页面只读取任务、总结与脱敏时间线，不提供任何 qBittorrent 或媒体文件操作。</div>
    <PageState :loading="store.detailLoading" :error="store.detailError" />

    <template v-if="job && store.summary && !store.detailLoading && !store.detailError">
      <section class="source-strip job-source">
        <div><span>媒体</span><strong>{{ store.summary.media.title }}</strong></div>
        <div><span>任务状态</span><StatusPill :status="job.status" /></div>
        <div><span>进度</span><strong>{{ formatProgress(job.progress) }}</strong></div>
        <div><span>H&amp;R</span><strong :class="job.hnr_status === 'SATISFIED' ? '' : 'risk-text'">{{ hnrLabel(job.hnr_status) }}</strong></div>
        <div><span>最后观测</span><strong>{{ formatShanghai(job.last_seen_at) }}</strong></div>
      </section>

      <div v-if="job.hnr_status === 'UNKNOWN'" class="notice-state warning-state job-hnr-warning">
        H&amp;R 状态为 UNKNOWN：不能根据下载完成、做种时间或 Ratio 推断站点义务已经满足。
      </div>

      <section class="media-import-entry">
        <div>
          <span class="eyebrow">MEDIA IMPORT PLAN</span>
          <strong>入库规划</strong>
          <small>仅规划，不操作媒体文件</small>
        </div>
        <div class="header-actions">
          <RouterLink class="button secondary" :to="{ path: '/media-imports', query: { download_job_id: job.id } }">查看关联规划</RouterLink>
          <RouterLink v-if="auth.hasRole('operator')" class="button primary" :to="{ path: '/media-imports/new', query: { download_job_id: job.id, source_root_ref: job.save_path_ref } }">创建入库规划</RouterLink>
        </div>
      </section>

      <div class="job-detail-grid">
        <section class="job-detail-section job-monitoring-section">
          <div class="job-section-heading"><div><span class="eyebrow">MONITORING</span><h2>下载与做种</h2></div><StatusPill :status="job.status" /></div>
          <strong class="job-release-title">{{ job.release_title }}</strong>
          <div class="job-progress-overview">
            <strong>{{ formatProgress(job.progress) }}</strong>
            <div><div class="job-progress-line"><span :style="{ width: formatProgress(job.progress) }"></span></div><small>{{ formatBytes(job.downloaded_bytes) }} / {{ formatBytes(job.size_bytes) }}</small></div>
          </div>
          <dl class="approval-facts job-facts">
            <div><dt>下载速度</dt><dd>↓ {{ formatRate(job.download_speed_bps) }}</dd></div>
            <div><dt>上传速度</dt><dd>↑ {{ formatRate(job.upload_speed_bps) }}</dd></div>
            <div><dt>Ratio</dt><dd>{{ formatRatio(job.ratio) }}</dd></div>
            <div><dt>H&amp;R</dt><dd :class="job.hnr_status === 'SATISFIED' ? '' : 'risk-text'">{{ hnrLabel(job.hnr_status) }}</dd></div>
            <div><dt>上传量</dt><dd>{{ formatBytes(job.uploaded_bytes) }}</dd></div>
            <div><dt>文件数</dt><dd>{{ job.file_count }}</dd></div>
            <div><dt>分类</dt><dd>{{ job.category || '—' }}</dd></div>
            <div><dt>保存位置引用</dt><dd class="mono">{{ job.save_path_ref }}</dd></div>
          </dl>
        </section>

        <section class="job-detail-section">
          <span class="eyebrow">MEDIA</span><h2>媒体信息</h2>
          <dl class="approval-facts job-facts">
            <div><dt>标题</dt><dd>{{ store.summary.media.title }}</dd></div>
            <div><dt>类型</dt><dd>{{ store.summary.media.media_type === 'tv' ? '剧集' : '电影' }}</dd></div>
            <div><dt>TMDB ID</dt><dd>{{ store.summary.media.tmdb_id ?? '—' }}</dd></div>
            <div><dt>年份</dt><dd>{{ store.summary.media.year ?? '—' }}</dd></div>
            <div><dt>季集</dt><dd>{{ episodeLabel(store.summary.media.season, store.summary.media.episodes) }}</dd></div>
            <div><dt>媒体 ID</dt><dd class="mono">{{ store.summary.media.id }}</dd></div>
          </dl>
        </section>

        <section class="job-detail-section">
          <span class="eyebrow">CONTROL RECORDS</span><h2>审批与执行</h2>
          <dl class="approval-facts job-facts">
            <div><dt>审批状态</dt><dd><StatusPill :status="store.summary.approval.status" /></dd></div>
            <div><dt>执行状态</dt><dd><StatusPill :status="store.summary.execution.status" /></dd></div>
            <div><dt>审批记录</dt><dd><RouterLink class="inline-record-link" :to="`/approvals/${store.summary.approval.id}`">{{ store.summary.approval.id }}</RouterLink></dd></div>
            <div><dt>执行记录</dt><dd><RouterLink class="inline-record-link" :to="`/executions/${store.summary.execution.id}`">{{ store.summary.execution.id }}</RouterLink></dd></div>
            <div><dt>启动模式</dt><dd>{{ store.summary.execution.launch_mode === 'ADD_PAUSED' ? '添加后暂停' : '立即开始' }}</dd></div>
            <div><dt>需要对账</dt><dd :class="store.summary.execution.requires_reconciliation ? 'risk-text' : ''">{{ store.summary.execution.requires_reconciliation ? '是' : '否' }}</dd></div>
            <div><dt>校验 / 提交 / 验证</dt><dd>{{ formatShanghai(store.summary.execution.validated_at) }} / {{ formatShanghai(store.summary.execution.submitted_at) }} / {{ formatShanghai(store.summary.execution.verified_at) }}</dd></div>
            <div><dt>审批过期</dt><dd>{{ formatShanghai(store.summary.approval.expires_at) }}</dd></div>
          </dl>
        </section>

        <section class="job-detail-section">
          <span class="eyebrow">INTEGRITY</span><h2>任务标识与时间</h2>
          <dl class="approval-facts job-facts">
            <div><dt>任务 ID</dt><dd class="mono">{{ job.id }}</dd></div>
            <div><dt>执行 ID</dt><dd class="mono">{{ job.execution_id }}</dd></div>
            <div><dt>Info hash v1</dt><dd class="mono">{{ job.info_hash_v1 || '—' }}</dd></div>
            <div><dt>Info hash v2</dt><dd class="mono">{{ job.info_hash_v2 || '—' }}</dd></div>
            <div><dt>开始</dt><dd>{{ formatShanghai(job.started_at) }}</dd></div>
            <div><dt>完成</dt><dd>{{ formatShanghai(job.completed_at) }}</dd></div>
            <div><dt>最后观测</dt><dd>{{ formatShanghai(job.last_seen_at) }}</dd></div>
            <div><dt>更新时间</dt><dd>{{ formatShanghai(job.updated_at) }}</dd></div>
          </dl>
        </section>

        <section class="job-detail-section job-wide-section">
          <span class="eyebrow">WARNINGS</span><h2>警告与错误</h2>
          <div v-if="store.summary.warnings.length" class="job-warning-list">
            <span v-for="warning in store.summary.warnings" :key="warning">{{ warning }}</span>
          </div>
          <p v-else class="empty-inline">服务端总结未附加警告。</p>
          <div v-if="job.error_code || job.error_message" class="job-runtime-error">
            <strong>{{ job.error_code || '任务错误' }}</strong><span>{{ job.error_message || '服务端未提供错误说明' }}</span>
          </div>
        </section>

        <section class="job-detail-section job-wide-section job-timeline-section">
          <span class="eyebrow">AUDIT TIMELINE</span><h2>审批 / 执行 / 任务时间线</h2>
          <div v-if="store.timeline.length" class="timeline job-timeline">
            <article v-for="(item, index) in store.timeline" :key="`${item.source}-${item.created_at}-${index}`">
              <span class="timeline-dot"></span>
              <div>
                <div class="job-timeline-heading"><span class="job-source-tag" :class="`source-${item.source}`">{{ sourceLabels[item.source] }}</span><strong>{{ item.event_type }}</strong><StatusPill :status="item.to_status" /></div>
                <small>{{ transitionLabel(item) }} · {{ item.actor }} · {{ formatShanghai(item.created_at) }}</small>
                <pre v-if="detailsLabel(item.sanitized_details)">{{ detailsLabel(item.sanitized_details) }}</pre>
              </div>
            </article>
          </div>
          <p v-else class="empty-inline">暂无时间线事件。</p>
        </section>
      </div>
    </template>
  </section>
</template>
