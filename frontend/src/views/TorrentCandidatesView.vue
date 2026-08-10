<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useTorrentStore } from '../stores/torrents'
import { formatShanghai } from '../utils/format'

const route = useRoute()
const store = useTorrentStore()
const mediaId = computed(() => String(route.params.id))
onMounted(() => store.load(mediaId.value))

function formatBytes(value: number | null): string {
  if (value === null) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`
}

function coverage(season: number | null, episodes: number[] | null): string {
  if (season === null) return '—'
  const seasonText = `S${String(season).padStart(2, '0')}`
  if (!episodes?.length) return `${seasonText} 季包`
  return `${seasonText} · ${episodes.map((item) => `E${String(item).padStart(2, '0')}`).join(', ')}`
}
</script>

<template>
  <section class="page wide-page">
    <PageHeader
      eyebrow="PT CANDIDATE REVIEW"
      title="PT 种子候选"
      description="按外部 ID、季集覆盖和偏好评分的 AvistaZ 只读搜索结果。"
    >
      <div class="header-actions">
        <a class="button secondary" href="/media">返回列表</a>
        <button class="button primary" :disabled="store.working" @click="store.create(mediaId)">
          {{ store.working ? '搜索中…' : '新建只读搜索' }}
        </button>
      </div>
    </PageHeader>

    <div class="phase-banner"><span>安全边界</span>当前阶段仅支持只读搜索，不提供 .torrent 下载或下载器操作。</div>
    <PageState :loading="store.loading" :error="store.error" />
    <div v-if="store.notice" class="notice-state">{{ store.notice }}</div>

    <template v-if="store.media && !store.loading">
      <div class="result-toolbar">
        <div><span class="eyebrow">MEDIA</span><strong>{{ store.media.title }}</strong><small>TMDB {{ store.media.tmdb_id ?? '未确认' }}</small></div>
        <label v-if="store.runs.length">搜索运行
          <select :value="store.selectedRun?.id" @change="store.selectRun(($event.target as HTMLSelectElement).value)">
            <option v-for="run in store.runs" :key="run.id" :value="run.id">{{ formatShanghai(run.created_at) }} · {{ run.status }} · {{ run.candidate_count }} 项</option>
          </select>
        </label>
        <StatusPill v-if="store.selectedRun" :status="store.selectedRun.status" />
      </div>

      <PageState
        :empty="!store.error && store.candidates.length === 0"
        empty-text="当前搜索运行没有候选"
      />
      <div v-if="store.candidates.length" class="candidate-table-wrap">
        <table class="candidate-table">
          <thead><tr><th>发布名 / 覆盖</th><th>规格</th><th>音频 / 字幕</th><th>大小 / 活跃度</th><th>促销 / H&R</th><th>匹配</th><th>审批</th></tr></thead>
          <tbody>
            <tr v-for="result in store.candidates" :key="result.id">
              <td class="release-cell"><strong>{{ result.candidate.release_title }}</strong><small>{{ coverage(result.candidate.season, result.candidate.episodes) }}</small></td>
              <td><strong>{{ result.candidate.resolution || '—' }}</strong><small>{{ result.candidate.source || '—' }} · {{ result.candidate.codec || '—' }}<template v-if="result.candidate.hdr?.length"> · {{ result.candidate.hdr.join(' / ') }}</template></small></td>
              <td><strong>{{ result.candidate.audio?.join(' / ') || '—' }}</strong><small>{{ result.candidate.subtitles?.join(' / ') || '—' }}</small></td>
              <td><strong>{{ formatBytes(result.candidate.size_bytes) }}</strong><small>{{ result.candidate.seeders ?? 0 }} 做种 · {{ result.candidate.leechers ?? 0 }} 下载</small></td>
              <td><strong>{{ result.candidate.download_factor === 0 ? 'Free' : result.candidate.download_factor ?? '未知' }}</strong><small>H&R {{ result.candidate.hit_and_run === null ? '未知' : result.candidate.hit_and_run ? '是' : '否' }}</small></td>
              <td class="score-cell"><strong>{{ Math.round(result.match_score * 100) }}</strong><div class="reason-list compact"><span v-for="reason in result.match_reasons" :key="reason">{{ reason }}</span></div><div v-if="result.warnings.length" class="warning-list compact"><span v-for="warning in result.warnings" :key="warning">{{ warning }}</span></div></td>
              <td><div class="row-actions"><a v-if="store.selectedRun" :href="`/media/${mediaId}/torrent-searches/${store.selectedRun.id}/candidates/${result.id}/approval`">人工审批</a></div></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>
