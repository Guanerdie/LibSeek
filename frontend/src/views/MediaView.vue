<script setup lang="ts">
import { onMounted } from 'vue'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import { useMediaStore } from '../stores/media'
import type { MediaItem } from '../types'
import { formatShanghai } from '../utils/format'
import { hasConfirmedIdentityStatus } from '../utils/identity'

const store = useMediaStore()
onMounted(() => store.load())

function episodeValue(value: number | null): string {
  return value === null ? '—' : String(value)
}

function canOpenTorrentCandidates(item: MediaItem): boolean {
  return hasConfirmedIdentityStatus(item.workflow_status)
}
</script>

<template>
  <section class="page">
    <PageHeader
      eyebrow="MISSING LIBRARY"
      title="未入库影视"
      description="来自 NextFind 的标准化缺失资源清单；未确认字段保持为空。"
    />

    <form class="filter-bar" @submit.prevent="store.applyFilters">
      <label class="search-field"><span>⌕</span><input v-model="store.query" aria-label="搜索标题" placeholder="搜索标题" /></label>
      <select v-model="store.mediaType" aria-label="影视类型"><option value="">全部类型</option><option value="movie">电影</option><option value="tv">电视剧</option></select>
      <select v-model="store.confidence" aria-label="识别置信度"><option value="">全部置信度</option><option value="HIGH">高</option><option value="NEEDS_CONFIRMATION">待确认</option></select>
      <button class="button primary" type="submit">应用筛选</button>
    </form>

    <PageState
      :loading="store.loading"
      :error="store.error"
      :empty="!store.loading && !store.error && store.items.length === 0"
      empty-text="当前没有未入库影视"
    />
    <div v-if="!store.loading && store.items.length" class="table-panel">
      <table>
        <thead><tr><th>标题</th><th>类型</th><th>TMDB ID</th><th>年份</th><th>本地 / 总集 / 已播</th><th>精确缺失</th><th>识别</th><th>发现时间</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="item in store.items" :key="item.id">
            <td><strong>{{ item.title }}</strong><small>{{ item.original_title || '—' }}</small></td>
            <td><span class="type-tag">{{ item.media_type === 'movie' ? '电影' : '电视剧' }}</span></td>
            <td class="mono">{{ item.tmdb_id ?? '待确认' }}</td>
            <td>{{ item.year ?? '—' }}</td>
            <td class="mono">{{ episodeValue(item.local_episodes) }} / {{ episodeValue(item.total_episodes) }} / {{ episodeValue(item.aired_episodes) }}</td>
            <td><span v-if="item.missing_episodes?.length" class="missing-list">{{ item.missing_episodes.join(', ') }}</span><span v-else>—</span></td>
            <td><span :class="['confidence', item.identity_confidence === 'HIGH' ? 'high' : 'confirm']">{{ item.identity_confidence === 'HIGH' ? '高' : '待确认' }}</span></td>
            <td>{{ formatShanghai(item.discovered_at) }}</td>
            <td>
              <div class="row-actions">
                <a :href="`/media/${item.id}/identity`">{{ canOpenTorrentCandidates(item) ? '身份' : '确认身份' }}</a>
                <a v-if="canOpenTorrentCandidates(item)" :href="`/media/${item.id}/torrents`">PT 候选</a>
                <button
                  v-else
                  class="button small"
                  type="button"
                  disabled
                  title="必须先完成身份确认"
                >
                  PT 候选（需先确认）
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <footer class="pagination">
        <span>共 {{ store.total }} 项 · 第 {{ store.page }} 页</span>
        <div><button class="button small" :disabled="store.page <= 1" @click="store.previousPage">上一页</button><button class="button small" :disabled="store.page * store.pageSize >= store.total" @click="store.nextPage">下一页</button></div>
      </footer>
    </div>
  </section>
</template>
