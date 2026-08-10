<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useQbittorrentStore } from '../stores/qbittorrent'
import { formatShanghai } from '../utils/format'

const store = useQbittorrentStore()

onMounted(() => store.load())
onBeforeUnmount(() => store.invalidate())

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

function formatProgress(value: number): string {
  return `${(value * 100).toFixed(value >= 1 ? 0 : 1)}%`
}

function formatRatio(value: number): string {
  return value < 0 ? '—' : value.toFixed(2)
}

function formatTimestamp(value: number): string {
  return value <= 0 ? '—' : formatShanghai(new Date(value * 1000).toISOString())
}
</script>

<template>
  <section class="page wide-page">
    <PageHeader
      eyebrow="QBITTORRENT READ-ONLY"
      title="qBittorrent 状态"
      description="查看下载器版本、连接概况和现有任务状态。"
    >
      <button class="button secondary" :disabled="store.loading" @click="store.load">刷新状态</button>
    </PageHeader>

    <div class="phase-banner"><span>只读</span>此页面只调用状态与任务查询接口，不会修改 qBittorrent。</div>
    <PageState :loading="store.loading" :error="store.error" />

    <template v-if="store.status && !store.loading">
      <section class="source-strip qb-summary">
        <div><span>连接</span><strong>{{ store.status.connected ? '正常' : '断开' }}</strong></div>
        <div><span>应用版本</span><strong class="mono">{{ store.status.application_version }}</strong></div>
        <div><span>Web API</span><strong class="mono">{{ store.status.web_api_version }}</strong></div>
        <div><span>任务 / 分类</span><strong>{{ store.status.torrent_count }} / {{ store.status.category_count }}</strong></div>
        <div><span>活跃做种</span><strong>{{ store.status.active_seeding_count }}</strong></div>
      </section>

      <PageState :empty="store.torrents.length === 0" empty-text="qBittorrent 当前没有任务" />
      <div v-if="store.torrents.length" class="candidate-table-wrap">
        <table class="qb-table">
          <thead>
            <tr><th>任务</th><th>状态 / 进度</th><th>大小</th><th>分享率 / 上传</th><th>分类 / 标签</th><th>时间</th><th>保存位置</th></tr>
          </thead>
          <tbody>
            <tr v-for="torrent in store.torrents" :key="torrent.hash">
              <td class="qb-name"><strong>{{ torrent.name }}</strong><small class="mono">{{ torrent.hash }}</small></td>
              <td><StatusPill :status="torrent.state" /><div class="progress-track" :aria-label="`进度 ${formatProgress(torrent.progress)}`"><span :style="{ width: formatProgress(torrent.progress) }"></span></div><small>{{ formatProgress(torrent.progress) }}</small></td>
              <td>{{ formatBytes(torrent.size) }}</td>
              <td><strong>{{ formatRatio(torrent.ratio) }}</strong><small>{{ formatBytes(torrent.uploaded) }} · {{ formatBytes(torrent.upspeed) }}/s</small></td>
              <td><strong>{{ torrent.category || '—' }}</strong><small>{{ torrent.tags || '—' }}</small></td>
              <td><strong>添加 {{ formatTimestamp(torrent.added_on) }}</strong><small>完成 {{ formatTimestamp(torrent.completion_on) }} · 做种 {{ Math.floor(torrent.seeding_time / 3600) }}h</small></td>
              <td class="qb-path mono">{{ torrent.save_path || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>
