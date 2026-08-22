<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useDailyStore } from '../stores/daily'
import { statusLabel } from '../utils/format'

const daily = useDailyStore()

onMounted(() => daily.loadMedia())
</script>

<template>
  <section class="page">
    <PageHeader
      eyebrow="DAILY WORKFLOW"
      title="缺失影视"
      description="从缺失列表选择一项，确认资源后直接进入下载。"
    >
      <button class="button primary" :disabled="daily.loading" @click="daily.syncMedia">
        {{ daily.loading ? '同步中…' : '同步缺失影视' }}
      </button>
    </PageHeader>

    <PageState
      :loading="daily.loading"
      :error="daily.error"
      :empty="!daily.loading && !daily.error && daily.media.length === 0"
      empty-text="当前没有缺失影视"
    />

    <div v-if="daily.media.length" class="media-grid">
      <article v-for="item in daily.media" :key="item.id" class="panel media-card">
        <div class="media-card-heading">
          <div>
            <span class="eyebrow">{{ item.media_type === 'tv' ? '电视剧' : '电影' }}</span>
            <h2>{{ item.title }}</h2>
            <p>{{ item.year ?? '年份未知' }} · TMDB {{ item.tmdb_id ?? '待识别' }}</p>
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
