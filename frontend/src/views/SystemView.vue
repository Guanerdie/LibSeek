<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import { useAuthStore } from '../stores/auth'
import { useDashboardStore } from '../stores/dashboard'
import type { DownloadJob, MediaItem } from '../types'
import { formatShanghai } from '../utils/format'
import { hasConfirmedIdentityStatus } from '../utils/identity'

const auth = useAuthStore()
const store = useDashboardStore()

const canDiscover = computed(() => auth.hasRole('operator'))
const nextfindConfigured = computed(() => Boolean(store.system?.nextfind_configured))
const discoveryDisabled = computed(
  () =>
    store.discoveryCreating ||
    store.systemLoading ||
    !canDiscover.value ||
    !nextfindConfigured.value,
)
const discoveryHint = computed(() => {
  if (!canDiscover.value) return '当前角色只读，需要操作者或管理员权限。'
  if (store.systemLoading) return '正在检查 NextFind 配置状态。'
  if (!store.system) return '系统状态不可用，暂时无法确认 NextFind 配置。'
  if (!store.system.nextfind_configured) return '请先完成 NextFind 本机配置。'
  return '从 NextFind 创建只读发现任务。'
})
const readiness = computed(() => {
  const status = store.system
  if (!status) return []
  return [
    {
      label: 'NextFind',
      ready: status.nextfind_configured,
      value: status.nextfind_configured ? '已配置' : '未配置',
    },
    {
      label: 'TMDB',
      ready: status.tmdb_live_enabled && status.tmdb_configured,
      value: status.tmdb_live_enabled && status.tmdb_configured ? '只读已启用' : '默认关闭',
    },
    {
      label: status.pt_site_label || 'PT 站点',
      ready: status.pt_site_search_ready,
      value: status.pt_site_status,
    },
    {
      label: 'qBittorrent',
      ready: status.qb_read_only_enabled && status.qb_configured,
      value: status.qb_status,
    },
  ]
})
const readinessSummary = computed(() => {
  if (!store.system || store.systemLoading) return '—'
  return `${readiness.value.filter((item) => item.ready).length} / ${readiness.value.length}`
})
const downloadGates = computed(() => {
  const status = store.system
  if (!status) return []
  return [
    { label: '人工执行控制面', enabled: status.download_control_plane_enabled },
    { label: '下载执行器', enabled: status.download_executor_enabled },
    { label: 'PT 站点取种', enabled: status.avistaz_torrent_fetch_enabled },
    { label: 'qBittorrent 写入', enabled: status.qb_write_enabled },
    { label: '下载监控', enabled: status.download_monitor_enabled },
    { label: '自动化引擎', enabled: status.automation_engine_enabled },
  ]
})
const downloadWriteSwitchesEnabled = computed(() => {
  const status = store.system
  return Boolean(
    status?.download_control_plane_enabled &&
      status.download_executor_enabled &&
      status.avistaz_torrent_fetch_enabled &&
      status.qb_write_enabled,
  )
})

onMounted(() => {
  void store.refresh()
})
onBeforeUnmount(() => store.cancelDiscoveryPolling())

function mediaAction(item: MediaItem): { to: string; label: string } {
  if (hasConfirmedIdentityStatus(item.workflow_status)) {
    return { to: `/media/${item.id}/torrents`, label: '选择 PT 种子' }
  }
  return { to: `/media/${item.id}/identity`, label: '确认影视信息' }
}

function mediaMeta(item: MediaItem): string {
  const parts = [item.media_type === 'movie' ? '电影' : '电视剧']
  if (item.year) parts.push(String(item.year))
  if (item.missing_episodes?.length) parts.push(item.missing_episodes.join(', '))
  return parts.join(' · ')
}

function formatProgress(value: number): string {
  return `${(Math.min(1, Math.max(0, value)) * 100).toFixed(1)}%`
}

function jobMeta(job: DownloadJob): string {
  return `${formatProgress(job.progress)} · 更新于 ${formatShanghai(job.updated_at)}`
}
</script>

<template>
  <section class="page dashboard-page">
    <PageHeader
      eyebrow="WORKSPACE"
      title="首页"
      description="处理未入库影视，跟进下载状态，并快速进入需要人工确认的节点。"
    >
      <div class="dashboard-primary-action">
        <button
          class="button primary"
          type="button"
          :disabled="discoveryDisabled"
          :title="discoveryHint"
          @click="store.createDiscovery"
        >
          {{ store.discoveryCreating ? '创建中…' : '获取未入库影视' }}
        </button>
        <small>{{ discoveryHint }}</small>
      </div>
    </PageHeader>

    <div
      v-if="store.discoveryNotice"
      :class="[
        'notice-state',
        'dashboard-notice',
        store.discoveryNoticeKind === 'success'
          ? 'success-state'
          : store.discoveryNoticeKind === 'warning'
            ? 'warning-state'
            : '',
      ]"
      role="status"
    >
      <span>{{ store.discoveryNotice }}</span>
      <RouterLink to="/discovery">查看发现任务</RouterLink>
    </div>
    <div v-if="store.discoveryError" class="notice-state error-state dashboard-notice" role="alert">
      {{ store.discoveryError }}
    </div>

    <section class="dashboard-status-band" aria-labelledby="dashboard-status-title">
      <header class="dashboard-section-heading">
        <div>
          <span class="eyebrow">SERVICE &amp; READINESS</span>
          <h2 id="dashboard-status-title">运行与配置</h2>
        </div>
        <RouterLink class="table-action" to="/configuration">连接配置</RouterLink>
      </header>

      <div v-if="store.systemLoading" class="dashboard-inline-state">正在检查运行状态…</div>
      <div v-else-if="store.systemError" class="dashboard-inline-state error-text" role="alert">
        {{ store.systemError }}
      </div>
      <template v-else-if="store.system">
        <div class="dashboard-health-list">
          <div
            v-for="(component, name) in {
              API: store.system.api,
              Worker: store.system.worker,
              PostgreSQL: store.system.postgres,
            }"
            :key="name"
          >
            <span class="health-dot" :class="component.healthy ? 'healthy' : 'unhealthy'"></span>
            <strong>{{ name }}</strong>
            <small>{{ component.healthy ? '正常' : '需检查' }}</small>
          </div>
        </div>
        <div class="dashboard-readiness-list">
          <div v-for="item in readiness" :key="item.label">
            <span>{{ item.label }}</span>
            <strong :class="['config-state', item.ready ? 'configured' : 'disabled']">{{ item.value }}</strong>
          </div>
        </div>
        <div class="dashboard-gate-heading">
          <div>
            <strong>下载执行门禁</strong>
            <small>这里只汇总写入开关；不代表凭据、目标策略或执行器心跳已经完整就绪。</small>
          </div>
          <strong :class="['config-state', downloadWriteSwitchesEnabled ? 'configured' : 'disabled']">
            {{ downloadWriteSwitchesEnabled ? '下载写入开关已开启' : '保持关闭' }}
          </strong>
        </div>
        <div class="dashboard-readiness-list download-gate-list">
          <div v-for="gate in downloadGates" :key="gate.label">
            <span>{{ gate.label }}</span>
            <strong :class="['config-state', gate.enabled ? 'configured' : 'disabled']">
              {{ gate.enabled ? '已启用' : '关闭' }}
            </strong>
          </div>
        </div>
      </template>
    </section>

    <div class="dashboard-metrics" aria-label="工作流摘要">
      <RouterLink to="/media" class="dashboard-metric">
        <span>未入库影视</span>
        <strong>{{ store.mediaLoading ? '—' : store.mediaTotal }}</strong>
        <small>进入识别与 PT 候选流程</small>
      </RouterLink>
      <RouterLink to="/download-jobs" class="dashboard-metric">
        <span>下载任务</span>
        <strong>{{ store.downloadsLoading ? '—' : store.downloadTotal }}</strong>
        <small>查看进度、风险和任务总结</small>
      </RouterLink>
      <RouterLink to="/configuration" class="dashboard-metric">
        <span>只读集成就绪</span>
        <strong>{{ readinessSummary }}</strong>
        <small>下载写入门禁单独展示，默认不计入</small>
      </RouterLink>
    </div>

    <div class="dashboard-work-grid">
      <section class="dashboard-list-section" aria-labelledby="recent-media-title">
        <header class="dashboard-section-heading">
          <div>
            <span class="eyebrow">MISSING MEDIA</span>
            <h2 id="recent-media-title">最近未入库</h2>
          </div>
          <RouterLink class="table-action" to="/media">查看全部</RouterLink>
        </header>
        <div v-if="store.mediaLoading" class="dashboard-inline-state">正在加载影视预览…</div>
        <div v-else-if="store.mediaError" class="dashboard-inline-state error-text" role="alert">
          {{ store.mediaError }}
        </div>
        <div v-else-if="store.mediaItems.length === 0" class="dashboard-inline-state">当前没有未入库影视</div>
        <ul v-else class="dashboard-item-list">
          <li v-for="item in store.mediaItems" :key="item.id">
            <div>
              <strong>{{ item.title }}</strong>
              <small v-if="item.original_title">{{ item.original_title }}</small>
              <span>{{ mediaMeta(item) }}</span>
            </div>
            <RouterLink class="button small secondary" :to="mediaAction(item).to">
              {{ mediaAction(item).label }}
            </RouterLink>
          </li>
        </ul>
      </section>

      <section class="dashboard-list-section" aria-labelledby="recent-downloads-title">
        <header class="dashboard-section-heading">
          <div>
            <span class="eyebrow">DOWNLOAD ACTIVITY</span>
            <h2 id="recent-downloads-title">最近下载任务</h2>
          </div>
          <RouterLink class="table-action" to="/download-jobs">查看全部</RouterLink>
        </header>
        <div v-if="store.downloadsLoading" class="dashboard-inline-state">正在加载下载任务…</div>
        <div v-else-if="store.downloadsError" class="dashboard-inline-state error-text" role="alert">
          {{ store.downloadsError }}
        </div>
        <div v-else-if="store.downloadJobs.length === 0" class="dashboard-inline-state">暂无下载任务</div>
        <ul v-else class="dashboard-item-list download-preview-list">
          <li v-for="job in store.downloadJobs" :key="job.id">
            <div>
              <div class="dashboard-item-title">
                <strong>{{ job.release_title }}</strong>
                <StatusPill :status="job.status" />
              </div>
              <small>{{ jobMeta(job) }}</small>
            </div>
            <RouterLink class="button small secondary" :to="`/download-jobs/${job.id}`">查看总结</RouterLink>
          </li>
        </ul>
      </section>
    </div>
  </section>
</template>
