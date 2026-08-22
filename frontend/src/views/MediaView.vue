<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import { useAuthStore } from '../stores/auth'
import { useMediaStore } from '../stores/media'
import { useDownloadBatchStore } from '../stores/downloadBatches'
import { ptSiteApi } from '../api/client'
import type { MediaItem, PtSiteCatalogItem } from '../types'
import { formatCountryCodes, formatShanghai } from '../utils/format'
import { hasConfirmedIdentityStatus } from '../utils/identity'
import { NEXTFIND_MEDIA_REGIONS } from '../utils/mediaRegions'

const auth = useAuthStore()
const store = useMediaStore()
const batchStore = useDownloadBatchStore()
const router = useRouter()
const selected = ref<string[]>([])
const showBatchForm = ref(false)
const batchName = ref('')
const batchMode = ref<'SEARCH_ONLY' | 'AUTO_SAFE'>('AUTO_SAFE')
const launchMode = ref<'ADD_PAUSED' | 'SCHEDULED_START'>('SCHEDULED_START')
const siteId = ref('')
const ptSites = ref<PtSiteCatalogItem[]>([])
const siteError = ref<string | null>(null)
const resolutions = ref('1080p')
const allCurrentSelected = computed({
  get: () => {
    const selectable = store.items.filter((item) => item.discovery_status === 'MISSING')
    return selectable.length > 0 && selectable.every((item) => selected.value.includes(item.id))
  },
  set: (checked: boolean) => {
    const currentIds = new Set(
      store.items.filter((item) => item.discovery_status === 'MISSING').map((item) => item.id),
    )
    selected.value = checked
      ? [...new Set([...selected.value, ...currentIds])]
      : selected.value.filter((id) => !currentIds.has(id))
  },
})
const canOperate = computed(() => auth.hasRole('operator'))
onMounted(async () => {
  await Promise.all([
    store.load(),
    ptSiteApi.catalog().then((catalog) => {
      ptSites.value = catalog.sites.filter((site) => site.available_for_search)
      siteId.value = catalog.default_site_id && ptSites.value.some((site) => site.site_id === catalog.default_site_id)
        ? catalog.default_site_id
        : ptSites.value[0]?.site_id ?? ''
    }).catch((caught) => {
      siteError.value = caught instanceof Error ? caught.message : '加载 PT 站点失败'
    }),
  ])
})
onBeforeUnmount(() => store.cancelSync())

function episodeValue(value: number | null): string {
  return value === null ? '—' : String(value)
}

function canOpenTorrentCandidates(item: MediaItem): boolean {
  return hasConfirmedIdentityStatus(item.workflow_status)
}

async function createBatch(): Promise<void> {
  const batch = await batchStore.create({
    name: batchName.value.trim() || `下载批次 ${new Date().toLocaleString('zh-CN')}`,
    media_ids: selected.value,
    mode: batchMode.value,
    launch_mode: batchMode.value === 'SEARCH_ONLY' ? 'ADD_PAUSED' : launchMode.value,
    site_id: siteId.value,
    preferred_resolutions: resolutions.value.split(',').map((v) => v.trim()).filter(Boolean),
    preferred_sources: [], preferred_audio: [], preferred_subtitles: [],
  })
  if (batch) await router.push(`/download-batches/${batch.id}`)
}
</script>

<template>
  <section class="page">
    <PageHeader
      eyebrow="MISSING LIBRARY"
      title="未入库影视"
      description="系统自动补全明确身份并搜索 PT；只有信息不明确时才需要人工确认。"
    >
      <div class="header-actions">
        <button
          class="button primary"
          type="button"
          :disabled="store.syncing || store.loading || !canOperate"
          :title="canOperate ? '从 NextFind 同步最新未入库影视' : '需要操作者权限'"
          @click="store.syncNextFind"
        >
          {{ store.syncing ? '同步中…' : '同步 NextFind' }}
        </button>
        <button class="button secondary" type="button" :disabled="!selected.length || !canOperate" @click="showBatchForm = !showBatchForm">创建下载批次（{{ selected.length }}）</button>
      </div>
    </PageHeader>

    <div v-if="store.syncNotice" class="notice-state" role="status">{{ store.syncNotice }}</div>
    <div v-if="store.syncError" class="notice-state error-state" role="alert">{{ store.syncError }}</div>
    <div v-if="batchStore.error" class="notice-state error-state" role="alert">{{ batchStore.error }}</div>
    <div v-if="siteError" class="notice-state error-state" role="alert">{{ siteError }}</div>

    <form v-if="showBatchForm" class="filter-bar batch-create-bar" @submit.prevent="createBatch">
      <input v-model="batchName" aria-label="批次名称" placeholder="批次名称" maxlength="120" />
      <select v-model="batchMode" aria-label="批次模式"><option value="AUTO_SAFE">安全自动</option><option value="SEARCH_ONLY">仅搜索</option></select>
      <select v-model="siteId" aria-label="PT 站点" required>
        <option disabled value="">选择 PT 站点</option>
        <option v-for="site in ptSites" :key="site.site_id" :value="site.site_id">{{ site.display_name }}</option>
      </select>
      <select v-if="batchMode === 'AUTO_SAFE'" v-model="launchMode" aria-label="启动策略">
        <option value="SCHEDULED_START">由 qB 队列调度</option>
        <option value="ADD_PAUSED">添加后暂停</option>
      </select>
      <input v-model="resolutions" aria-label="分辨率偏好" placeholder="1080p,2160p" />
      <button class="button primary" type="submit" :disabled="batchStore.working || !siteId">{{ batchStore.working ? '创建中…' : '确认创建' }}</button>
    </form>

    <form class="filter-bar" @submit.prevent="store.applyFilters">
      <label class="search-field"><span>⌕</span><input v-model="store.query" aria-label="搜索标题" placeholder="搜索标题" /></label>
      <select v-model="store.mediaType" aria-label="影视类型"><option value="">全部类型</option><option value="movie">电影</option><option value="tv">电视剧</option></select>
      <select v-model="store.region" aria-label="地区">
        <option value="">全部地区</option>
        <option v-for="option in NEXTFIND_MEDIA_REGIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
      </select>
      <select v-model="store.confidence" aria-label="识别置信度"><option value="">全部置信度</option><option value="HIGH">高</option><option value="NEEDS_CONFIRMATION">待确认</option></select>
      <select v-model="store.discoveryStatus" aria-label="入库状态"><option value="MISSING">未入库</option><option value="IN_LIBRARY">已入库历史</option><option value="ALL">全部状态</option></select>
      <button class="button primary" type="submit">应用筛选</button>
    </form>

    <PageState
      :loading="store.loading"
      :error="store.error"
      :empty="!store.loading && !store.error && store.items.length === 0"
      empty-text="当前没有未入库影视"
    />
    <div v-if="!store.loading && store.items.length" class="table-panel">
      <table class="media-table">
        <thead><tr><th><input v-model="allCurrentSelected" type="checkbox" aria-label="选择当前页" /></th><th>标题</th><th>类型</th><th>TMDB ID</th><th>年份</th><th>国家 / 地区</th><th>本地 / 总集 / 已播</th><th>精确缺失</th><th>识别</th><th>发现时间</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="item in store.items" :key="item.id">
            <td><input v-model="selected" type="checkbox" :value="item.id" :disabled="item.discovery_status !== 'MISSING'" :aria-label="`选择 ${item.title}`" /></td>
            <td><strong>{{ item.title }}</strong><small>{{ item.original_title || '—' }}</small></td>
            <td><span class="type-tag">{{ item.media_type === 'movie' ? '电影' : '电视剧' }}</span></td>
            <td class="mono">{{ item.tmdb_id ?? '待确认' }}</td>
            <td>{{ item.year ?? '—' }}</td>
            <td data-testid="media-country">{{ formatCountryCodes(item.country_codes) || '待确认' }}</td>
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
