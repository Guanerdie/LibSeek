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

const outcome = computed(() => daily.selectedMedia?.latest_automation ?? null)

/** The rejection reasons that actually mattered, most common first. */
const outcomeReasons = computed(() => {
  const counts = new Map<string, number>()
  for (const item of outcome.value?.rejected ?? []) {
    for (const reason of item.reasons) {
      counts.set(reason, (counts.get(reason) ?? 0) + 1)
    }
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6)
})

const subscribing = ref(false)

async function toggleSubscription(): Promise<void> {
  if (subscribing.value || !daily.selectedMedia) return
  subscribing.value = true
  try {
    await daily.setSubscription(mediaId.value, !daily.selectedMedia.subscribed)
  } finally {
    subscribing.value = false
  }
}

function outcomeTime(iso: string): string {
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString()
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
      <button
        v-if="daily.selectedMedia"
        class="button"
        :class="{ primary: !daily.selectedMedia.subscribed }"
        :disabled="subscribing"
        @click="toggleSubscription"
      >
        {{ daily.selectedMedia.subscribed ? '取消追更' : '追这部' }}
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

    <div
      v-if="daily.selectedMedia?.subscribed && !daily.selectedMedia?.subscription_active"
      class="panel compact-panel outcome-warning"
    >
      <strong>已加入追更清单，但当前不会生效</strong>
      <span class="muted">
        自动化的范围模式是「按筛选条件」，不读这份清单。去自动化设置页改成「手动选择」才会生效。
      </span>
    </div>

    <div v-if="outcome" class="panel outcome-panel">
      <div class="outcome-head">
        <strong>最近一次自动化</strong>
        <span class="muted">{{ outcomeTime(outcome.created_at) }}</span>
      </div>
      <p v-if="outcome.selected_title" class="outcome-line">
        选中了
        <strong>{{ outcome.selected_title }}</strong>
        <span v-if="outcome.selected_score !== null" class="muted">
          （评分 {{ outcome.selected_score.toFixed(3) }}）
        </span>
      </p>
      <p v-else-if="outcome.candidate_count === 0" class="outcome-line">
        站点没有搜到任何候选资源。
      </p>
      <p v-else class="outcome-line">
        搜到 <strong>{{ outcome.candidate_count }}</strong> 个候选，但一个都不符合当前策略：
      </p>
      <ul v-if="!outcome.selected_title && outcomeReasons.length" class="outcome-reasons">
        <li v-for="[reason, count] in outcomeReasons" :key="reason">
          <span class="outcome-count">{{ count }}</span> {{ reason }}
        </li>
      </ul>
      <p v-if="outcome.download_skipped" class="outcome-line muted">
        没有提交下载：{{ outcome.download_skipped }}
      </p>
      <p v-if="outcome.error_message" class="outcome-line muted">
        错误：{{ outcome.error_message }}
        <span v-if="outcome.error_code">（{{ outcome.error_code }}）</span>
      </p>
      <p v-if="outcome.search_cooldown_until" class="outcome-line muted">
        下次搜索时间：{{ outcomeTime(outcome.search_cooldown_until) }}
      </p>
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
