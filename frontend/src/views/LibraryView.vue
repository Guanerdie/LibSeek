<script setup lang="ts">
import { onMounted, reactive } from 'vue'
import { RouterLink } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useDailyStore } from '../stores/daily'
import type { DailyMediaRegion, DailyMediaState, DailyMediaType } from '../types'
import { statusLabel } from '../utils/format'

const daily = useDailyStore()
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
              {{ statusLabel(state) }}
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
          <StatusPill :status="item.state" :label="statusLabel(item.state)" />
        </div>
        <p v-if="item.attention_reason" class="inline-warning">{{ item.attention_reason }}</p>
        <RouterLink class="button primary" :to="`/library/${item.id}/resources`">
          {{ item.tmdb_id ? '查找资源' : '确认影视信息' }}
        </RouterLink>
      </article>
    </div>
  </section>
</template>
