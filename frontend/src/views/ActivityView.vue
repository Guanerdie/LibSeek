<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { activityApi, ApiError } from '../api/client'
import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import Pagination from '../components/Pagination.vue'
import type { ActivityCategory, ActivityEntry, ActivityTrigger } from '../types'
import {
  actorLabel,
  categoryLabel,
  detailRows,
  eventLabel,
  reasonText,
  triggerLabel,
} from '../utils/activity'
import { formatShanghai } from '../utils/format'

const categories: ActivityCategory[] = ['download', 'cleanup', 'library', 'search', 'settings']

const filters = reactive<{ category: ActivityCategory | ''; trigger: ActivityTrigger | ''; query: string }>({
  category: '',
  trigger: '',
  query: '',
})
const entries = ref<ActivityEntry[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 30
const loading = ref(true)
const error = ref<string | null>(null)
const openId = ref<string | null>(null)

async function load(requestedPage = 1): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const response = await activityApi.list({
      page: requestedPage,
      pageSize,
      category: filters.category || undefined,
      trigger: filters.trigger || undefined,
      query: filters.query.trim() || undefined,
    })
    entries.value = response.items
    total.value = response.total
    page.value = response.page
    openId.value = null
  } catch (caught) {
    entries.value = []
    error.value = caught instanceof ApiError ? caught.message : '无法读取操作记录'
  } finally {
    loading.value = false
  }
}

function reset(): void {
  filters.category = ''
  filters.trigger = ''
  filters.query = ''
  void load(1)
}

function toggle(entry: ActivityEntry): void {
  openId.value = openId.value === entry.id ? null : entry.id
}

function runId(entry: ActivityEntry): string | null {
  const value = entry.details.run_id
  return typeof value === 'string' ? value : null
}

onMounted(() => void load(1))
</script>

<template>
  <section class="page activity-page">
    <PageHeader
      eyebrow="ACTIVITY"
      title="操作记录"
      description="每一次下载、删种和设置修改：什么时候、谁、做了什么、手动还是自动、为什么。"
    >
      <button class="button secondary" :disabled="loading" @click="load(page)">刷新</button>
    </PageHeader>

    <form class="panel library-filters" @submit.prevent="load(1)">
      <div class="filter-grid activity-filter-grid">
        <label>
          搜索
          <input v-model="filters.query" type="search" placeholder="影视名称、种子名称或操作人" />
        </label>
        <label>
          类型
          <select v-model="filters.category" aria-label="操作类型">
            <option value="">全部</option>
            <option v-for="item in categories" :key="item" :value="item">{{ categoryLabel(item) }}</option>
          </select>
        </label>
        <label>
          方式
          <select v-model="filters.trigger" aria-label="手动或自动">
            <option value="">全部</option>
            <option value="MANUAL">手动</option>
            <option value="AUTO">自动</option>
          </select>
        </label>
      </div>
      <div class="filter-actions">
        <button class="button secondary" type="button" @click="reset">清空</button>
        <button class="button primary" type="submit">筛选</button>
      </div>
    </form>

    <PageState
      :loading="loading"
      :error="error"
      :empty="!loading && !error && entries.length === 0"
      empty-text="没有符合条件的操作记录"
    />

    <div v-if="!loading && entries.length" class="panel activity-list">
      <article
        v-for="entry in entries"
        :key="entry.id"
        class="activity-row"
        :class="{ 'is-open': openId === entry.id }"
      >
        <button
          class="activity-summary"
          type="button"
          :aria-expanded="openId === entry.id"
          @click="toggle(entry)"
        >
          <time class="activity-time">{{ formatShanghai(entry.created_at) }}</time>
          <span class="activity-what">
            <strong>{{ eventLabel(entry.event) }}</strong>
            <span>{{ entry.media_title ?? entry.message }}</span>
          </span>
          <span class="activity-who">{{ actorLabel(entry.actor) }}</span>
          <span class="activity-tags">
            <span class="status-pill" :class="`activity-${entry.category}`">{{ categoryLabel(entry.category) }}</span>
            <span
              class="status-pill"
              :class="entry.trigger === 'MANUAL' ? 'activity-manual' : 'activity-auto'"
            >
              {{ triggerLabel(entry.trigger) }}
            </span>
          </span>
        </button>

        <dl v-if="openId === entry.id" class="activity-detail">
          <div><dt>时间</dt><dd>{{ formatShanghai(entry.created_at) }}</dd></div>
          <div><dt>操作人</dt><dd>{{ actorLabel(entry.actor) }}</dd></div>
          <div><dt>方式</dt><dd>{{ triggerLabel(entry.trigger) }}</dd></div>
          <div><dt>操作</dt><dd>{{ entry.message }}</dd></div>
          <div class="activity-detail-wide"><dt>原因</dt><dd>{{ reasonText(entry) }}</dd></div>
          <div v-if="entry.media_id">
            <dt>影视</dt>
            <dd>
              <RouterLink :to="`/library/${encodeURIComponent(entry.media_id)}/resources`">
                {{ entry.media_title ?? '查看影视' }}
              </RouterLink>
            </dd>
          </div>
          <div v-if="runId(entry)">
            <dt>来源</dt>
            <dd><RouterLink :to="`/automation/runs/${runId(entry)}`">查看这次自动化运行</RouterLink></dd>
          </div>
          <div v-for="row in detailRows(entry.details)" :key="row.key">
            <dt>{{ row.label }}</dt>
            <dd>{{ row.value }}</dd>
          </div>
        </dl>
      </article>
    </div>

    <Pagination :page="page" :total="total" :page-size="pageSize" label="操作记录分页" @change="load" />
  </section>
</template>
