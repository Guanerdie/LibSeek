<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import Pagination from '../components/Pagination.vue'
import StatusPill from '../components/StatusPill.vue'
import { useDailyStore } from '../stores/daily'
import type { DailyMediaRegion, DailyMediaState, DailyMediaType } from '../types'
import { mediaStateLabel } from '../utils/format'

const daily = useDailyStore()
const summary = computed(() => {
  const items = daily.media
  return [
    { label: '缺失条目', value: daily.mediaTotal, icon: '▤', tone: 'violet' },
    { label: '待识别', value: items.filter((item) => !item.tmdb_id).length, icon: '○', tone: 'cyan' },
    { label: '可用资源', value: items.filter((item) => item.state === 'CANDIDATES').length, icon: '◇', tone: 'blue' },
    { label: '需要关注', value: items.filter((item) => Boolean(item.attention_reason)).length, icon: '!', tone: 'pink' },
  ]
})
const filters = reactive<{
  query: string
  mediaType: DailyMediaType | ''
  region: DailyMediaRegion | ''
  state: DailyMediaState | ''
  year: string
}>({
  query: daily.mediaQuery.query ?? '',
  mediaType: daily.mediaQuery.mediaType ?? '',
  region: daily.mediaQuery.region ?? '',
  state: daily.mediaQuery.state ?? '',
  year: daily.mediaQuery.year ? String(daily.mediaQuery.year) : '',
})

const bulkBusy = ref(false)
const bulkNotice = ref<string | null>(null)

/** Add every item the current filter produced to the follow list. */
async function subscribeCurrentPage(subscribed: boolean): Promise<void> {
  if (bulkBusy.value || daily.media.length === 0) return
  bulkBusy.value = true
  bulkNotice.value = null
  try {
    const changed = await daily.setSubscriptions(
      daily.media.map((item) => item.id),
      subscribed,
    )
    if (changed !== null) {
      bulkNotice.value = subscribed
        ? `已把本页 ${daily.media.length} 项加入追更清单（新增 ${changed} 项）`
        : `已从追更清单移除本页 ${daily.media.length} 项（减少 ${changed} 项）`
    }
  } finally {
    bulkBusy.value = false
  }
}

function applyFilters(): void {
  void daily.loadMedia({
    query: filters.query.trim() || undefined,
    mediaType: filters.mediaType || undefined,
    region: filters.region || undefined,
    state: filters.state || undefined,
    year: filters.year ? Number(filters.year) : undefined,
  })
}

function resetFilters(): void {
  filters.query = ''
  filters.mediaType = ''
  filters.region = ''
  filters.state = ''
  filters.year = ''
  void daily.loadMedia({})
}

function changePage(page: number): void {
  void daily.loadMedia({ ...daily.mediaQuery, page })
}

onMounted(() => daily.loadMedia())
</script>

<template>
  <section class="page">
    <PageHeader
      eyebrow="DAILY WORKFLOW"
      title="缺失影视"
      description="从缺失列表选择一项，确认资源后直接进入下载。"
    >
      <button class="button primary" :disabled="daily.mediaSyncing" @click="daily.syncMedia">
        {{ daily.mediaSyncing ? '同步中…' : '同步缺失影视' }}
      </button>
    </PageHeader>

    <div class="summary-grid" aria-label="影视概览">
      <div v-for="item in summary" :key="item.label" class="summary-card">
        <span :class="['summary-icon', `tone-${item.tone}`]">{{ item.icon }}</span>
        <span><small>{{ item.label }}</small><strong>{{ item.value }}</strong></span>
      </div>
    </div>

    <form class="panel library-filters" @submit.prevent="applyFilters">
      <div class="filter-heading">
        <div>
          <span class="eyebrow">FILTERS</span>
          <strong>筛选缺失影视</strong>
        </div>
        <span class="muted">{{ daily.mediaTotal }} 项结果</span>
      </div>
      <div class="filter-grid">
        <label>
          关键词
          <input v-model="filters.query" type="search" placeholder="中文名、原名" />
        </label>
        <label>
          影视类型
          <select v-model="filters.mediaType">
            <option value="">全部类型</option>
            <option v-for="type in daily.mediaFilterOptions.media_types" :key="type" :value="type">
              {{ type === 'tv' ? '电视剧' : '电影' }}
            </option>
          </select>
        </label>
        <label>
          地区
          <select v-model="filters.region">
            <option value="">全部地区</option>
            <option v-for="region in daily.mediaFilterOptions.regions" :key="region" :value="region">
              {{ region }}
            </option>
          </select>
        </label>
        <label>
          状态
          <select v-model="filters.state">
            <option value="">全部状态</option>
            <option v-for="state in daily.mediaFilterOptions.states" :key="state" :value="state">
              {{ mediaStateLabel(state) }}
            </option>
          </select>
        </label>
        <label>
          年份
          <select v-model="filters.year">
            <option value="">全部年份</option>
            <option v-for="year in daily.mediaFilterOptions.years" :key="year" :value="year">
              {{ year }}
            </option>
          </select>
        </label>
      </div>
      <div class="filter-actions">
        <button class="button secondary" type="button" :disabled="daily.mediaListLoading" @click="resetFilters">
          重置
        </button>
        <button class="button primary" type="submit" :disabled="daily.mediaListLoading">
          {{ daily.mediaListLoading ? '筛选中…' : '应用筛选' }}
        </button>
      </div>
    </form>

    <div v-if="daily.media.length" class="filter-actions bulk-actions">
      <span v-if="bulkNotice" class="muted">{{ bulkNotice }}</span>
      <span v-else class="muted">
        追更清单决定自动化处理哪些影视；先用上面的条件筛出想要的，再整页加入。
      </span>
      <button
        class="button"
        type="button"
        :disabled="bulkBusy"
        @click="subscribeCurrentPage(false)"
      >
        本页移出追更
      </button>
      <button
        class="button primary"
        type="button"
        :disabled="bulkBusy"
        @click="subscribeCurrentPage(true)"
      >
        本页加入追更（{{ daily.media.length }} 项）
      </button>
    </div>

    <PageState
      :loading="daily.mediaListLoading"
      :error="daily.mediaError"
      :empty="!daily.mediaListLoading && !daily.mediaError && daily.media.length === 0"
      empty-text="当前没有缺失影视"
    />

    <div v-if="daily.media.length" class="media-grid">
      <article v-for="item in daily.media" :key="item.id" class="panel media-card">
        <div class="media-card-heading">
          <div>
            <span class="eyebrow">{{ item.media_type === 'tv' ? '电视剧' : '电影' }}</span>
            <h2>{{ item.title }}</h2>
            <p>
              {{ item.year ?? '年份未知' }} ·
              {{ item.regions.length ? item.regions.join(' / ') : '地区未知' }} ·
              TMDB {{ item.tmdb_id ?? '待识别' }}
            </p>
          </div>
          <StatusPill :status="item.state" :label="mediaStateLabel(item.state)" />
        </div>
        <p v-if="item.attention_reason" class="inline-warning">{{ item.attention_reason }}</p>
        <RouterLink class="card-action" :to="`/library/${item.id}/resources`">
          {{ item.tmdb_id ? '查找资源' : '确认影视信息' }}
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>
        </RouterLink>
      </article>
    </div>
    <Pagination
      :page="daily.mediaPage"
      :total="daily.mediaTotal"
      :page-size="daily.mediaPageSize"
      label="缺失影视分页"
      @change="changePage"
    />
  </section>
</template>
