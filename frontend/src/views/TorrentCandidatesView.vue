<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useAuthStore } from '../stores/auth'
import { useTorrentStore } from '../stores/torrents'
import type { PtSiteCatalogItem, TorrentSearchCreateRequest } from '../types'
import { formatShanghai } from '../utils/format'

const route = useRoute()
const auth = useAuthStore()
const store = useTorrentStore()
const mediaId = computed(() => String(route.params.id))
const canOperate = computed(() => auth.hasRole('operator'))
const selectedSite = computed(() =>
  store.sites.find((site) => site.site_id === store.selectedSiteId),
)
const selectedSiteSupportsMedia = computed(() =>
  Boolean(
    selectedSite.value &&
      store.media &&
      selectedSite.value.media_types.includes(store.media.media_type),
  ),
)
const canCreateSearch = computed(
  () =>
    canOperate.value &&
    !store.catalogLoading &&
    !store.catalogError &&
    Boolean(selectedSite.value?.available_for_search) &&
    selectedSiteSupportsMedia.value,
)
const resolutionOptions = ['2160p', '1080p', '720p']
const sourceOptions = ['BluRay', 'WEB-DL', 'WEBRip', 'HDTV']
const preferredResolutions = ref<string[]>(['2160p', '1080p'])
const preferredSources = ref<string[]>(['BluRay', 'WEB-DL'])
const preferredAudio = ref('')
const preferredSubtitles = ref('Chinese, 中文')
const maxSizeGiB = ref('')

watch(
  mediaId,
  (value) => {
    void store.loadCatalog()
    void store.load(value)
  },
  { immediate: true },
)
onBeforeUnmount(() => store.cancelSelection())

function refresh(): void {
  void store.loadCatalog()
  void store.load(mediaId.value)
}

function parseList(value: string): string[] {
  return [...new Set(value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean))]
}

const preferenceError = computed(() => {
  const audio = parseList(preferredAudio.value)
  const subtitles = parseList(preferredSubtitles.value)
  if (audio.length > 10 || subtitles.length > 10) return '音频和字幕偏好分别最多填写 10 项'
  if ([...audio, ...subtitles].some((item) => item.length > 80)) return '单个音频或字幕偏好不能超过 80 个字符'
  const rawSize = maxSizeGiB.value.trim()
  if (!rawSize) return null
  const size = Number(rawSize)
  if (!Number.isFinite(size) || size <= 0 || size > 10_240) return '最大体积必须大于 0 且不超过 10240 GiB'
  return null
})

function resetPreferences(): void {
  preferredResolutions.value = ['2160p', '1080p']
  preferredSources.value = ['BluRay', 'WEB-DL']
  preferredAudio.value = ''
  preferredSubtitles.value = 'Chinese, 中文'
  maxSizeGiB.value = ''
}

function createSearch(): void {
  if (preferenceError.value || !canCreateSearch.value || !selectedSite.value) return
  const payload: TorrentSearchCreateRequest = {
    site_id: selectedSite.value.site_id,
    preferred_resolutions: [...preferredResolutions.value],
    preferred_sources: [...preferredSources.value],
    preferred_audio: parseList(preferredAudio.value),
    preferred_subtitles: parseList(preferredSubtitles.value),
  }
  const rawSize = maxSizeGiB.value.trim()
  if (rawSize) payload.max_size_bytes = Math.floor(Number(rawSize) * 1024 ** 3)
  void store.create(mediaId.value, payload)
}

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

function siteStateLabel(site: PtSiteCatalogItem): string {
  if (site.available_for_search) return '可搜索'
  return site.mode.toUpperCase().includes('DISABLED') ? '已禁用' : '不可用'
}

function siteOptionLabel(site: PtSiteCatalogItem): string {
  const defaultLabel = site.site_id === store.defaultSiteId ? ' · 默认' : ''
  return `${site.display_name} (${site.site_id})${defaultLabel} · ${siteStateLabel(site)}`
}

function siteIdentity(siteId: string): string {
  const site = store.sites.find((item) => item.site_id === siteId)
  return site ? `${site.display_name} (${siteId})` : `${siteId}（当前目录未登记）`
}

function mediaTypesLabel(site: PtSiteCatalogItem): string {
  return site.media_types.map((value) => (value === 'movie' ? '电影' : '电视剧')).join(' / ') || '无'
}

function searchModesLabel(site: PtSiteCatalogItem): string {
  return site.search_modes.join(' / ') || '未声明'
}
</script>

<template>
  <section class="page wide-page torrent-candidates-page">
    <PageHeader
      eyebrow="PT CANDIDATE REVIEW"
      title="PT 种子候选"
      description="按站点、外部 ID、季集覆盖和偏好评分查看只读搜索结果。"
    >
      <div class="header-actions">
        <a class="button secondary" href="/media">返回列表</a>
        <button class="button secondary" :disabled="store.loading || store.selecting || store.working" @click="refresh">刷新结果</button>
      </div>
    </PageHeader>

    <div class="phase-banner"><span>安全边界</span>当前阶段仅支持只读搜索；本页面不提供 .torrent 获取或下载器操作。</div>
    <div class="permission-bar compact-permission">
      <strong>当前账号：{{ auth.principal?.username }} · {{ auth.roleLabel }}</strong>
      <small>{{ canOperate ? '可创建 PT 搜索与审批请求' : '当前角色仅可查看；创建搜索与审批请求需要操作者权限' }}</small>
    </div>
    <PageState :loading="store.loading" :error="store.error" />
    <div v-if="store.notice" class="notice-state">{{ store.notice }}</div>

    <template v-if="store.media && !store.loading">
      <form class="search-preferences" @submit.prevent="createSearch">
        <div class="preference-heading"><div><span class="eyebrow">SEARCH PREFERENCES</span><h2>PT 搜索偏好</h2></div><button class="button small" type="button" :disabled="store.working" @click="resetPreferences">恢复默认</button></div>

        <div class="pt-site-selection">
          <label>PT 站点
            <select v-model="store.selectedSiteId" aria-label="PT 站点" :disabled="store.catalogLoading || store.working || store.sites.length === 0">
              <option v-if="store.catalogLoading" value="">正在加载站点目录</option>
              <option v-else-if="store.sites.length === 0" value="">没有可用站点</option>
              <option v-for="site in store.sites" :key="site.site_id" :value="site.site_id">{{ siteOptionLabel(site) }}</option>
            </select>
          </label>
          <div v-if="selectedSite" class="pt-site-capabilities">
            <div class="pt-site-heading"><strong>{{ selectedSite.display_name }}</strong><span :class="['config-state', selectedSite.available_for_search ? 'configured' : 'disabled']">{{ siteStateLabel(selectedSite) }}</span></div>
            <p>{{ selectedSite.description }}</p>
            <div class="pt-site-capability-list">
              <span>模式 {{ selectedSite.mode }}</span>
              <span>搜索 {{ searchModesLabel(selectedSite) }}</span>
              <span>媒体 {{ mediaTypesLabel(selectedSite) }}</span>
              <span>{{ selectedSite.manual_only ? '仅人工流程' : '允许策略编排' }}</span>
              <span :class="{ off: !selectedSite.promotion_metadata }">促销元数据 {{ selectedSite.promotion_metadata ? '支持' : '未知' }}</span>
              <span :class="{ off: !selectedSite.hit_and_run_metadata }">H&amp;R 元数据 {{ selectedSite.hit_and_run_metadata ? '支持' : '未知' }}</span>
              <span :class="{ off: !selectedSite.torrent_fetch_enabled }">种子获取 {{ selectedSite.torrent_fetch_enabled ? '已启用' : '未启用' }}</span>
            </div>
            <small v-if="!selectedSiteSupportsMedia" class="pt-site-unavailable">该站点不支持当前媒体类型</small>
            <small v-else-if="!selectedSite.available_for_search" class="pt-site-unavailable">{{ selectedSite.unavailable_reason_message || selectedSite.unavailable_reason_code || '该站点当前不可搜索' }}</small>
          </div>
        </div>
        <div v-if="store.catalogError" class="notice-state error-state pt-site-catalog-error" role="alert">{{ store.catalogError }}</div>

        <fieldset>
          <legend>分辨率</legend>
          <div class="option-row"><label v-for="option in resolutionOptions" :key="option"><input v-model="preferredResolutions" type="checkbox" :value="option" />{{ option }}</label></div>
        </fieldset>
        <fieldset>
          <legend>来源</legend>
          <div class="option-row"><label v-for="option in sourceOptions" :key="option"><input v-model="preferredSources" type="checkbox" :value="option" />{{ option }}</label></div>
        </fieldset>
        <label>音频<input v-model="preferredAudio" data-testid="preferred-audio" maxlength="900" placeholder="Japanese, English" /></label>
        <label>字幕<input v-model="preferredSubtitles" data-testid="preferred-subtitles" maxlength="900" placeholder="Chinese, English" /></label>
        <label>最大体积（GiB）<input v-model="maxSizeGiB" data-testid="max-size-gib" inputmode="decimal" placeholder="不限制" :aria-invalid="Boolean(preferenceError)" /></label>
        <div class="preference-submit"><span v-if="preferenceError" class="preference-error">{{ preferenceError }}</span><button class="button primary" type="submit" :disabled="store.working || Boolean(preferenceError) || !canCreateSearch">{{ store.working ? '搜索中…' : '新建只读搜索' }}</button></div>
      </form>

      <div v-if="store.actionError" class="notice-state error-state" role="alert">{{ store.actionError }}</div>

      <div class="result-toolbar">
        <div><span class="eyebrow">MEDIA</span><strong>{{ store.media.title }}</strong><small>TMDB {{ store.media.tmdb_id ?? '未确认' }}<template v-if="store.selectedRun"> · {{ siteIdentity(store.selectedRun.site_id) }}</template></small></div>
        <label v-if="store.runs.length">搜索运行
          <select :value="store.selectedRunId ?? ''" @change="store.selectRun(($event.target as HTMLSelectElement).value)">
            <option v-for="run in store.runs" :key="run.id" :value="run.id">{{ siteIdentity(run.site_id) }} · {{ formatShanghai(run.created_at) }} · {{ run.status }} · {{ run.candidate_count }} 项</option>
          </select>
        </label>
        <StatusPill v-if="store.selectedRun" :status="store.selectedRun.status" />
      </div>

      <div v-if="store.selectionError" class="notice-state error-state" role="alert">{{ store.selectionError }}</div>
      <PageState
        :loading="store.selecting"
        :empty="!store.selectionError && store.candidates.length === 0"
        empty-text="当前搜索运行没有候选"
      />
      <div v-if="!store.selecting && store.candidates.length" class="candidate-table-wrap torrent-candidate-desktop-list">
        <table class="candidate-table">
          <thead><tr><th>发布名 / 覆盖</th><th>规格</th><th>音频 / 字幕</th><th>大小 / 活跃度</th><th>促销 / H&R</th><th>匹配</th><th>审批</th></tr></thead>
          <tbody>
            <tr v-for="result in store.candidates" :key="result.id">
              <td class="release-cell"><span class="site-id-badge">{{ siteIdentity(result.candidate.site_id) }}</span><strong>{{ result.candidate.release_title }}</strong><small>{{ coverage(result.candidate.season, result.candidate.episodes) }}</small></td>
              <td><strong>{{ result.candidate.resolution || '—' }}</strong><small>{{ result.candidate.source || '—' }} · {{ result.candidate.codec || '—' }}<template v-if="result.candidate.hdr?.length"> · {{ result.candidate.hdr.join(' / ') }}</template></small></td>
              <td><strong>{{ result.candidate.audio?.join(' / ') || '—' }}</strong><small>{{ result.candidate.subtitles?.join(' / ') || '—' }}</small></td>
              <td><strong>{{ formatBytes(result.candidate.size_bytes) }}</strong><small>{{ result.candidate.seeders ?? 0 }} 做种 · {{ result.candidate.leechers ?? 0 }} 下载</small></td>
              <td><strong>{{ result.candidate.download_factor === 0 ? 'Free' : result.candidate.download_factor ?? '未知' }}</strong><small>H&amp;R {{ result.candidate.hit_and_run === null ? '未知' : result.candidate.hit_and_run ? '是' : '否' }}</small></td>
              <td class="score-cell"><strong>{{ Math.round(result.match_score * 100) }}</strong><div class="reason-list compact"><span v-for="reason in result.match_reasons" :key="reason">{{ reason }}</span></div><div v-if="result.warnings.length" class="warning-list compact"><span v-for="warning in result.warnings" :key="warning">{{ warning }}</span></div></td>
              <td><div class="row-actions"><a v-if="store.selectedRun" :href="`/media/${mediaId}/torrent-searches/${store.selectedRun.id}/candidates/${result.id}/approval`">人工审批</a></div></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="!store.selecting && store.candidates.length" class="torrent-candidate-mobile-list">
        <article v-for="result in store.candidates" :key="result.id" class="torrent-candidate-mobile-item">
          <header><span class="site-id-badge">{{ siteIdentity(result.candidate.site_id) }}</span><strong class="mobile-score">{{ Math.round(result.match_score * 100) }}</strong></header>
          <h2>{{ result.candidate.release_title }}</h2>
          <small>{{ coverage(result.candidate.season, result.candidate.episodes) }}</small>
          <dl>
            <div><dt>规格</dt><dd>{{ result.candidate.resolution || '—' }} · {{ result.candidate.source || '—' }} · {{ result.candidate.codec || '—' }}</dd></div>
            <div><dt>音频 / 字幕</dt><dd>{{ result.candidate.audio?.join(' / ') || '—' }} / {{ result.candidate.subtitles?.join(' / ') || '—' }}</dd></div>
            <div><dt>大小 / 活跃</dt><dd>{{ formatBytes(result.candidate.size_bytes) }} · {{ result.candidate.seeders ?? 0 }} 做种 · {{ result.candidate.leechers ?? 0 }} 下载</dd></div>
            <div><dt>促销 / H&amp;R</dt><dd>{{ result.candidate.download_factor === 0 ? 'Free' : result.candidate.download_factor ?? '未知' }} / {{ result.candidate.hit_and_run === null ? '未知' : result.candidate.hit_and_run ? '是' : '否' }}</dd></div>
          </dl>
          <div class="reason-list compact"><span v-for="reason in result.match_reasons" :key="reason">{{ reason }}</span></div>
          <div v-if="result.warnings.length" class="warning-list compact"><span v-for="warning in result.warnings" :key="warning">{{ warning }}</span></div>
          <a v-if="store.selectedRun" class="button secondary" :href="`/media/${mediaId}/torrent-searches/${store.selectedRun.id}/candidates/${result.id}/approval`">人工审批</a>
        </article>
      </div>
    </template>
  </section>
</template>
