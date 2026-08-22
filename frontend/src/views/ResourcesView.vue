<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useDailyStore } from '../stores/daily'
import type { DailyCandidate } from '../types'
import { statusLabel } from '../utils/format'

const route = useRoute()
const router = useRouter()
const daily = useDailyStore()
const mediaId = computed(() => String(route.params.id))
const candidates = computed(() => daily.search?.candidates ?? [])
const tmdbId = ref('')

function formatBytes(value: number | null): string {
  if (value === null) return '大小未知'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(unit >= 3 ? 1 : 0)} ${units[unit]}`
}

async function startSearch(): Promise<void> {
  await daily.startSearch(mediaId.value, ['avistaz'])
  if (daily.search) {
    await router.replace({ query: { search: daily.search.id } })
  }
}

async function identify(): Promise<void> {
  const parsed = Number(tmdbId.value)
  await daily.identify(mediaId.value, Number.isInteger(parsed) && parsed > 0 ? parsed : undefined)
}

async function download(candidate: DailyCandidate): Promise<void> {
  const confirmed =
    candidate.warnings.length === 0 ||
    globalThis.confirm(`这个资源有以下提示：\n\n${candidate.warnings.join('\n')}\n\n仍然下载吗？`)
  if (!confirmed) return
  if (await daily.download(candidate.id, candidate.warnings.length > 0)) {
    await router.push('/downloads')
  }
}

onMounted(async () => {
  await daily.loadMediaDetail(mediaId.value)
  const searchId = typeof route.query.search === 'string' ? route.query.search : null
  if (searchId) await daily.loadSearch(searchId)
})
</script>

<template>
  <section class="page">
    <PageHeader
      eyebrow="CHOOSE RELEASE"
      :title="daily.selectedMedia?.title ?? '选择资源'"
      description="候选按匹配程度排序；有风险提示时只需确认一次。"
    >
      <button
        class="button primary"
        :disabled="daily.loading || !daily.selectedMedia?.tmdb_id"
        @click="startSearch"
      >
        搜索 PT 资源
      </button>
    </PageHeader>

    <PageState :loading="daily.loading" :error="daily.error" />

    <div v-if="daily.selectedMedia && !daily.selectedMedia.tmdb_id" class="panel identity-prompt">
      <div>
        <strong>确认 TMDB 影视信息</strong>
        <p>可直接搜索；如果同名结果较多，请输入明确的 TMDB ID。</p>
      </div>
      <input v-model="tmdbId" inputmode="numeric" placeholder="可选：TMDB ID" />
      <button class="button primary" @click="identify">确认影视信息</button>
    </div>

    <div v-if="daily.selectedMedia?.episodes.length" class="panel compact-panel">
      <strong>缺集</strong>
      <span class="muted">
        {{
          daily.selectedMedia.episodes
            .filter((episode) => episode.state === 'MISSING')
            .map((episode) => `S${String(episode.season_number).padStart(2, '0')}E${String(episode.episode_number).padStart(2, '0')}`)
            .join('、') || '没有缺集'
        }}
      </span>
    </div>

    <div v-if="daily.search" class="section-heading">
      <div>
        <span class="eyebrow">SEARCH RESULT</span>
        <h2>候选资源</h2>
      </div>
      <StatusPill :status="daily.search.state" :label="statusLabel(daily.search.state)" />
    </div>

    <PageState
      v-if="daily.search?.state === 'SUCCEEDED'"
      :empty="candidates.length === 0"
      empty-text="没有找到符合条件的资源"
    />

    <div v-if="candidates.length" class="candidate-stack">
      <article v-for="candidate in candidates" :key="candidate.id" class="panel candidate-card">
        <div class="candidate-main">
          <div>
            <span class="eyebrow">{{ candidate.site_id }}</span>
            <h3>{{ candidate.title }}</h3>
            <p>
              {{ formatBytes(candidate.size_bytes) }} · 做种 {{ candidate.seeders ?? '未知' }} ·
              {{ candidate.resolution ?? '规格未知' }} {{ candidate.source ?? '' }}
            </p>
          </div>
          <strong class="candidate-score">{{ Math.round(candidate.score * 100) }}</strong>
        </div>
        <ul v-if="candidate.reasons.length" class="reason-list">
          <li v-for="reason in candidate.reasons" :key="reason">{{ reason }}</li>
        </ul>
        <div v-if="candidate.warnings.length" class="inline-warning">
          {{ candidate.warnings.join('；') }}
        </div>
        <button class="button primary" @click="download(candidate)">
          {{ candidate.warnings.length ? '确认并下载' : '下载' }}
        </button>
      </article>
    </div>
  </section>
</template>
