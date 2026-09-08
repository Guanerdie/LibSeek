<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useDailyStore } from '../stores/daily'
import type { DailyCandidate } from '../types'
import { candidateReasonLabel, candidateWarningLabel, statusLabel } from '../utils/format'

const route = useRoute()
const router = useRouter()
const daily = useDailyStore()
const mediaId = computed(() => String(route.params.id))
const candidates = computed(() => daily.search?.candidates ?? [])
const tmdbId = ref('')
const submittingCandidateId = ref<string | null>(null)
const failedCandidateId = ref<string | null>(null)

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

function freeStatusLabel(downloadFactor: number | null): string {
  if (downloadFactor === null) return '免费状态未知'
  if (downloadFactor === 0) return '免费'
  if (downloadFactor < 1) return `非免费 · ${Math.round(downloadFactor * 100)}% 计费`
  return '非免费'
}

function freeStatusClass(downloadFactor: number | null): string {
  if (downloadFactor === null) return 'is-unknown'
  return downloadFactor === 0 ? 'is-free' : 'is-paid'
}

async function startSearch(force = false): Promise<void> {
  await daily.startSearch(mediaId.value, ['avistaz'], force)
  if (daily.search) {
    await router.replace({ query: { search: daily.search.id } })
  }
}

async function identify(): Promise<void> {
  const parsed = Number(tmdbId.value)
  await daily.identify(mediaId.value, Number.isInteger(parsed) && parsed > 0 ? parsed : undefined)
}

async function download(candidate: DailyCandidate): Promise<void> {
  if (submittingCandidateId.value) return
  const confirmed =
    candidate.warnings.length === 0 ||
    globalThis.confirm(
      `这个资源有以下提示：\n\n${candidate.warnings.map(candidateWarningLabel).join('\n')}\n\n仍然下载吗？`,
    )
  if (!confirmed) return
  failedCandidateId.value = null
  submittingCandidateId.value = candidate.id
  try {
    if (await daily.download(candidate.id, candidate.warnings.length > 0)) {
      await router.push('/downloads')
    } else {
      failedCandidateId.value = candidate.id
    }
  } finally {
    submittingCandidateId.value = null
  }
}

onMounted(async () => {
  await daily.loadMediaDetail(mediaId.value)
  const querySearchId = typeof route.query.search === 'string' ? route.query.search : null
  const searchId =
    querySearchId ?? (daily.search?.state === 'SUCCEEDED' ? daily.search.id : null)
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
        :disabled="daily.mediaDetailLoading || !daily.selectedMedia?.tmdb_id"
        @click="startSearch(false)"
      >
        搜索 PT 资源
      </button>
      <button
        class="button"
        :disabled="daily.mediaDetailLoading || !daily.selectedMedia?.tmdb_id"
        title="跳过 5 分钟内的缓存结果，重新向站点发起搜索"
        @click="startSearch(true)"
      >
        强制刷新
      </button>
    </PageHeader>

    <PageState :loading="daily.mediaDetailLoading" :error="daily.resourceError" />

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
    <PageState
      v-else-if="daily.search?.state === 'FAILED'"
      :error="daily.search.error_message || '搜索失败'"
    />

    <div v-if="candidates.length" class="candidate-stack">
      <article v-for="candidate in candidates" :key="candidate.id" class="panel candidate-card">
        <div class="candidate-main">
          <div>
            <div class="candidate-site-row">
              <span class="eyebrow">{{ candidate.site_id }}</span>
              <span
                class="candidate-free-status"
                :class="freeStatusClass(candidate.download_factor)"
              >
                {{ freeStatusLabel(candidate.download_factor) }}
              </span>
            </div>
            <h3>
              <a
                v-if="candidate.details_url"
                class="candidate-title-link"
                :href="candidate.details_url"
                target="_blank"
                rel="noopener noreferrer"
              >
                {{ candidate.title }}
              </a>
              <template v-else>{{ candidate.title }}</template>
            </h3>
            <p>
              {{ formatBytes(candidate.size_bytes) }} · 做种 {{ candidate.seeders ?? '未知' }} ·
              {{ candidate.resolution ?? '规格未知' }} {{ candidate.source ?? '' }}
            </p>
          </div>
          <strong class="candidate-score">{{ Math.round(candidate.score * 100) }}</strong>
        </div>
        <ul v-if="candidate.reasons.length" class="reason-list">
          <li v-for="reason in candidate.reasons" :key="reason">
            {{ candidateReasonLabel(reason) }}
          </li>
        </ul>
        <div v-if="candidate.warnings.length" class="inline-warning">
          {{ candidate.warnings.map(candidateWarningLabel).join('；') }}
        </div>
        <p
          v-if="failedCandidateId === candidate.id && daily.resourceError"
          class="inline-error"
          role="alert"
        >
          {{ daily.resourceError }}
        </p>
        <button
          class="button primary"
          :disabled="submittingCandidateId !== null"
          @click="download(candidate)"
        >
          {{
            submittingCandidateId === candidate.id
              ? '提交中…'
              : failedCandidateId === candidate.id
                ? '重试下载'
                : candidate.warnings.length
                  ? '确认并下载'
                  : '下载'
          }}
        </button>
      </article>
    </div>
  </section>
</template>
