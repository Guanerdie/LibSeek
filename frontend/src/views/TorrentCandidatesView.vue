<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useAuthStore } from '../stores/auth'
import { useTorrentStore } from '../stores/torrents'
import type {
  PtSiteCatalogItem,
  TorrentCandidateResult,
  TorrentSearchCreateRequest,
} from '../types'
import { formatCountryCodes, formatShanghai } from '../utils/format'
import { formatMediaRegions } from '../utils/mediaRegions'

const route = useRoute()
const auth = useAuthStore()
const store = useTorrentStore()
const mediaId = computed(() => String(route.params.id))
const canOperate = computed(() => auth.hasRole('operator'))
const canDownload = computed(() => auth.hasRole('admin'))
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
    store.identityGatePassed &&
    !store.catalogLoading &&
    !store.catalogError &&
    Boolean(selectedSite.value?.available_for_search) &&
    selectedSiteSupportsMedia.value,
)
const mediaSummary = computed(() => {
  if (!store.media) return ''
  return [
    `TMDB ${store.media.tmdb_id ?? '未确认'}`,
    `国家 / 地区 ${formatCountryCodes(store.media.country_codes) || '待确认'}`,
    `地区分组 ${formatMediaRegions(store.media.country_codes) || '待确认'}`,
  ].join(' · ')
})
const resolutionOptions = ['2160p', '1080p', '720p']
const sourceOptions = ['BluRay', 'WEB-DL', 'WEBRip', 'HDTV']
const preferredResolutions = ref<string[]>(['2160p', '1080p'])
const preferredSources = ref<string[]>(['BluRay', 'WEB-DL'])
const preferredAudio = ref('')
const preferredSubtitles = ref('Chinese, 中文')
const maxSizeGiB = ref('')
const selectedDownloadCandidate = ref<TorrentCandidateResult | null>(null)
const visiblePreflightChecks = computed(() =>
  store.downloadResult?.preflight.checks.filter((check) => check.status !== 'PASS') ?? [],
)

watch(
  mediaId,
  (value) => {
    selectedDownloadCandidate.value = null
    void store.loadCatalog()
    void store.load(value)
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  store.cancelAction()
  store.cancelRunPolling()
  store.cancelSelection()
})

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

function openDownloadConfirmation(result: TorrentCandidateResult): void {
  if (!canDownload.value || store.working) return
  store.resetDownloadConfirmation()
  selectedDownloadCandidate.value = result
}

function closeDownloadConfirmation(): void {
  if (store.working) return
  selectedDownloadCandidate.value = null
  store.resetDownloadConfirmation()
}

async function confirmSelectedDownload(): Promise<void> {
  if (!selectedDownloadCandidate.value || !canDownload.value || store.working) return
  await store.confirmDownload(selectedDownloadCandidate.value.id, 'START_IMMEDIATELY')
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
      eyebrow="PT SEARCH RESULTS"
      title="选择种子"
      description="按站点、季集覆盖和资源规格选择要下载的候选。"
    >
      <div class="header-actions">
        <a class="button secondary" href="/media">返回列表</a>
        <button class="button secondary" :disabled="store.loading || store.selecting || store.autoRefreshing || store.working" @click="refresh">
          {{ store.autoRefreshing ? '自动刷新中…' : '刷新结果' }}
        </button>
      </div>
    </PageHeader>

    <div class="phase-banner"><span>下载流程</span>选择候选并确认一次，系统将自动完成 qBittorrent 预检和任务提交。</div>
    <div class="permission-bar compact-permission">
      <strong>当前账号：{{ auth.principal?.username }} · {{ auth.roleLabel }}</strong>
      <small>{{ canDownload ? '可搜索并确认下载' : canOperate ? '可创建 PT 搜索；确认下载需要管理员权限' : '当前角色仅可查看' }}</small>
    </div>
    <PageState :loading="store.loading" :error="store.error" />
    <div v-if="store.notice" class="notice-state">{{ store.notice }}</div>

    <template v-if="store.media && !store.loading">
      <div v-if="!store.identityGatePassed" class="notice-state identity-required" role="status">
        <strong>需要先确认影视身份</strong>
        <span>
          {{ canOperate
            ? 'PT 搜索必须关联一条已确认的身份审核记录。'
            : '当前角色可查看身份候选；身份确认需要操作者权限。' }}
        </span>
        <a class="button secondary" :href="`/media/${mediaId}/identity`">前往身份确认</a>
      </div>

      <details v-else class="search-preferences-panel" :open="store.runs.length === 0">
        <summary>
          <span>手动重新搜索</span>
          <small>需要更换站点或调整规格时使用</small>
        </summary>
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
      </details>

      <div v-if="store.actionError" class="notice-state error-state" role="alert">{{ store.actionError }}</div>

      <div class="result-toolbar">
        <div>
          <span class="eyebrow">MEDIA</span>
          <strong>{{ store.media.title }}</strong>
          <small>{{ mediaSummary }}<template v-if="store.selectedRun"> · {{ siteIdentity(store.selectedRun.site_id) }}</template></small>
        </div>
        <label v-if="store.runs.length">搜索运行
          <select
            :value="store.selectedRunId ?? ''"
            :disabled="store.working || store.selecting"
            @change="store.selectRun(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="run in store.runs" :key="run.id" :value="run.id">{{ siteIdentity(run.site_id) }} · {{ formatShanghai(run.created_at) }} · {{ run.status }} · {{ run.candidate_count }} 项</option>
          </select>
        </label>
        <StatusPill v-if="store.selectedRun" :status="store.selectedRun.status" />
      </div>

      <div
        v-if="store.selectedRun?.error_code || store.selectedRun?.error_message"
        class="notice-state error-state"
        role="alert"
      >
        <strong>{{ store.selectedRun.error_code || 'PT_SEARCH_FAILED' }}</strong>
        <span>{{ store.selectedRun.error_message || 'PT 搜索失败，服务端未提供详细说明。' }}</span>
      </div>

      <div v-if="store.selectionError" class="notice-state error-state" role="alert">{{ store.selectionError }}</div>
      <PageState
        :loading="store.selecting"
        :empty="!store.selectionError && store.candidates.length === 0"
        empty-text="当前搜索运行没有候选"
      />
      <div v-if="!store.selecting && store.candidates.length" class="candidate-table-wrap torrent-candidate-desktop-list">
        <table class="candidate-table">
          <thead><tr><th>发布名 / 覆盖</th><th>规格</th><th>音频 / 字幕</th><th>大小 / 活跃度</th><th>促销 / H&R</th><th>匹配</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="result in store.candidates" :key="result.id">
              <td class="release-cell"><span class="site-id-badge">{{ siteIdentity(result.candidate.site_id) }}</span><strong>{{ result.candidate.release_title }}</strong><small>{{ coverage(result.candidate.season, result.candidate.episodes) }}</small></td>
              <td><strong>{{ result.candidate.resolution || '—' }}</strong><small>{{ result.candidate.source || '—' }} · {{ result.candidate.codec || '—' }}<template v-if="result.candidate.hdr?.length"> · {{ result.candidate.hdr.join(' / ') }}</template></small></td>
              <td><strong>{{ result.candidate.audio?.join(' / ') || '—' }}</strong><small>{{ result.candidate.subtitles?.join(' / ') || '—' }}</small></td>
              <td><strong>{{ formatBytes(result.candidate.size_bytes) }}</strong><small>{{ result.candidate.seeders ?? 0 }} 做种 · {{ result.candidate.leechers ?? 0 }} 下载</small></td>
              <td><strong>{{ result.candidate.download_factor === 0 ? 'Free' : result.candidate.download_factor ?? '未知' }}</strong><small>H&amp;R {{ result.candidate.hit_and_run === null ? '未知' : result.candidate.hit_and_run ? '是' : '否' }}</small></td>
              <td class="score-cell"><strong>{{ Math.round(result.match_score * 100) }}</strong><div class="reason-list compact"><span v-for="reason in result.match_reasons" :key="reason">{{ reason }}</span></div><div v-if="result.warnings.length" class="warning-list compact"><span v-for="warning in result.warnings" :key="warning">{{ warning }}</span></div></td>
              <td>
                <div class="row-actions">
                  <button
                    class="button primary small candidate-download-button"
                    type="button"
                    :disabled="!canDownload || store.working"
                    :title="canDownload ? '确认下载' : '需要管理员权限'"
                    @click="openDownloadConfirmation(result)"
                  >
                    确认下载
                  </button>
                </div>
              </td>
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
          <button
            class="button primary candidate-download-button"
            type="button"
            :disabled="!canDownload || store.working"
            @click="openDownloadConfirmation(result)"
          >
            确认下载
          </button>
        </article>
      </div>
    </template>

    <div
      v-if="selectedDownloadCandidate"
      class="download-confirm-backdrop"
      role="presentation"
      @click.self="closeDownloadConfirmation"
      @keydown.esc="closeDownloadConfirmation"
    >
      <section
        class="download-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="download-confirm-title"
        tabindex="-1"
      >
        <header>
          <div>
            <span class="eyebrow">CONFIRM DOWNLOAD</span>
            <h2 id="download-confirm-title">确认下载</h2>
          </div>
          <button
            class="dialog-close"
            type="button"
            aria-label="关闭"
            :disabled="store.working"
            @click="closeDownloadConfirmation"
          >
            ×
          </button>
        </header>

        <template v-if="!store.downloadResult">
          <strong class="download-release-title">
            {{ selectedDownloadCandidate.candidate.release_title }}
          </strong>
          <dl class="download-confirm-facts">
            <div><dt>站点</dt><dd>{{ siteIdentity(selectedDownloadCandidate.candidate.site_id) }}</dd></div>
            <div><dt>覆盖</dt><dd>{{ coverage(selectedDownloadCandidate.candidate.season, selectedDownloadCandidate.candidate.episodes) }}</dd></div>
            <div><dt>大小</dt><dd>{{ formatBytes(selectedDownloadCandidate.candidate.size_bytes) }}</dd></div>
            <div><dt>做种数</dt><dd>{{ selectedDownloadCandidate.candidate.seeders ?? 0 }}</dd></div>
          </dl>
          <p class="download-confirm-note">
            确认后将先运行 qBittorrent 只读预检；通过后立即开始下载，并按站点规则继续做种。
          </p>
          <div v-if="store.actionError" class="notice-state error-state" role="alert">
            {{ store.actionError }}
          </div>
          <footer>
            <button class="button secondary" type="button" :disabled="store.working" @click="closeDownloadConfirmation">取消</button>
            <button
              class="button primary"
              type="button"
              data-testid="confirm-candidate-download"
              :disabled="store.working"
              @click="confirmSelectedDownload"
            >
              {{ store.working ? '正在预检…' : store.actionError ? '重试' : '确认并下载' }}
            </button>
          </footer>
        </template>

        <template v-else-if="store.downloadResult.outcome === 'PREFLIGHT_BLOCKED'">
          <div class="download-result-heading blocked">
            <StatusPill status="BLOCKED" />
            <div><h3>预检未通过</h3><p>尚未向 qBittorrent 添加任务。</p></div>
          </div>
          <div class="preflight-list compact-preflight-list">
            <article
              v-for="check in visiblePreflightChecks"
              :key="check.code"
              :class="`check-${check.status.toLowerCase()}`"
            >
              <strong>{{ check.status }}</strong>
              <div><b>{{ check.code }}</b><p>{{ check.message }}</p></div>
            </article>
          </div>
          <footer>
            <button class="button secondary" type="button" @click="closeDownloadConfirmation">关闭</button>
            <button class="button primary" type="button" :disabled="store.working" @click="confirmSelectedDownload">重新预检</button>
          </footer>
        </template>

        <template v-else>
          <div class="download-result-heading success">
            <StatusPill :status="store.downloadResult.execution?.status ?? 'PENDING'" />
            <div><h3>已加入下载队列</h3><p>qBittorrent 预检已通过，后台正在提交任务。</p></div>
          </div>
          <div v-if="visiblePreflightChecks.length" class="preflight-list compact-preflight-list">
            <article
              v-for="check in visiblePreflightChecks"
              :key="check.code"
              :class="`check-${check.status.toLowerCase()}`"
            >
              <strong>{{ check.status }}</strong>
              <div><b>{{ check.code }}</b><p>{{ check.message }}</p></div>
            </article>
          </div>
          <footer>
            <button class="button secondary" type="button" @click="closeDownloadConfirmation">关闭</button>
            <a
              v-if="store.downloadResult.execution"
              class="button primary"
              href="/download-jobs"
            >查看下载任务</a>
          </footer>
        </template>
      </section>
    </div>
  </section>
</template>
