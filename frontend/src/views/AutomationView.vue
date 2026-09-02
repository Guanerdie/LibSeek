<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import { ApiError, automationApi, dailyApi } from '../api/client'
import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import Pagination from '../components/Pagination.vue'
import StatusPill from '../components/StatusPill.vue'
import type {
  AutomationJob,
  AutomationPolicy,
  AutomationRun,
  DailyMedia,
  DailyMediaRegion,
  DailyMediaType,
} from '../types'
import { formatShanghai } from '../utils/format'

const form = reactive({
  enabled: false,
  dry_run: true,
  auto_identify: true,
  scope_mode: 'filters' as 'filters' | 'selected',
  regions: [] as DailyMediaRegion[],
  selected_media_ids: [] as string[],
  site_ids: ['avistaz'],
  media_types: ['movie', 'tv'] as Array<'movie' | 'tv'>,
  minimum_score: 0.7,
  minimum_seeders: 1,
  max_size_gib: '',
  allow_warnings: false,
  interval_minutes: 60,
  retry_delay_minutes: 30,
  max_attempts: 3,
  daily_download_limit: 3,
  daily_download_gib: '',
})
const jobs = ref<AutomationJob[]>([])
const runs = ref<AutomationRun[]>([])
const runsTotal = ref(0)
const runsPage = ref(1)
const runsPageSize = 10
const runsLoading = ref(false)
const loading = ref(true)
const saving = ref(false)
const starting = ref(false)
const error = ref<string | null>(null)
const feedback = ref<string | null>(null)
const currentRun = ref<AutomationRun | null>(null)
const mediaOptions = ref<DailyMedia[]>([])
const mediaOptionsTotal = ref(0)
const mediaOptionsPage = ref(1)
const mediaOptionsPageSize = 10
const mediaOptionsLoading = ref(false)
const mediaQuery = reactive({
  query: '',
  region: '' as DailyMediaRegion | '',
  mediaType: '' as DailyMediaType | '',
})
const regionOptions: DailyMediaRegion[] = ['欧美', '大陆', '港台', '韩国', '日本', '亚太']
const runIsActive = computed(
  () => currentRun.value?.state === 'PENDING' || currentRun.value?.state === 'RUNNING',
)
const runCompleted = computed(() => {
  const run = currentRun.value
  return run ? run.succeeded + run.failed + run.deferred : 0
})
const runProgress = computed(() => {
  const run = currentRun.value
  if (!run) return 0
  if (run.created === 0) return runIsActive.value ? 0 : 100
  return Math.min(100, Math.round((runCompleted.value / run.created) * 100))
})
// Keep the old jobs fallback for older API deployments and unit-test mocks.
const runsApiAvailable = computed(() => typeof automationApi.runs === 'function')

const pollIntervalMs = 2_000
let pollTimer: ReturnType<typeof globalThis.setTimeout> | undefined
let pollGeneration = 0
let isMounted = false

function applyPolicy(policy: AutomationPolicy): void {
  form.enabled = policy.enabled
  form.dry_run = policy.dry_run
  form.auto_identify = policy.auto_identify
  form.scope_mode = policy.scope_mode
  form.regions = [...policy.regions]
  form.selected_media_ids = [...policy.selected_media_ids]
  form.site_ids = [...policy.site_ids]
  form.media_types = [...policy.media_types]
  form.minimum_score = policy.minimum_score
  form.minimum_seeders = policy.minimum_seeders
  form.max_size_gib = policy.max_size_bytes
    ? String(Math.round((policy.max_size_bytes / 1024 ** 3) * 10) / 10)
    : ''
  form.allow_warnings = policy.allow_warnings
  form.interval_minutes = policy.interval_minutes
  form.retry_delay_minutes = policy.retry_delay_minutes
  form.max_attempts = policy.max_attempts
  form.daily_download_limit = policy.daily_download_limit
  form.daily_download_gib = policy.daily_download_bytes
    ? String(Math.round((policy.daily_download_bytes / 1024 ** 3) * 10) / 10)
    : ''
}

async function loadMediaOptions(page = 1): Promise<void> {
  mediaOptionsLoading.value = true
  try {
    const response = await dailyApi.media({
      page,
      pageSize: mediaOptionsPageSize,
      query: mediaQuery.query.trim() || undefined,
      region: mediaQuery.region || undefined,
      mediaType: mediaQuery.mediaType || undefined,
    })
    mediaOptions.value = response.items
    mediaOptionsTotal.value = response.total
    mediaOptionsPage.value = response.page
  } catch (caught) {
    error.value = message(caught, '无法读取影视选择列表')
  } finally {
    mediaOptionsLoading.value = false
  }
}

function changeMediaOptionsPage(page: number): void {
  void loadMediaOptions(page)
}

function toggleMedia(mediaId: string): void {
  const index = form.selected_media_ids.indexOf(mediaId)
  if (index >= 0) form.selected_media_ids.splice(index, 1)
  else form.selected_media_ids.push(mediaId)
}

function message(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback
}

function isActive(run: AutomationRun): boolean {
  return run.state === 'PENDING' || run.state === 'RUNNING'
}

function runSummary(run: AutomationRun): string {
  const summary = `自动搜索${run.state === 'FAILED' ? '失败' : '完成'}：处理 ${run.created} 项，成功 ${run.succeeded} 项，失败 ${run.failed} 项`
  return run.deferred > 0 ? `${summary}，等待后续执行 ${run.deferred} 项` : summary
}

function stopPolling(): void {
  pollGeneration += 1
  if (pollTimer !== undefined) {
    globalThis.clearTimeout(pollTimer)
    pollTimer = undefined
  }
}

async function refreshJobs(): Promise<void> {
  jobs.value = (await automationApi.jobs()).items
}

async function loadRuns(page = runsPage.value): Promise<void> {
  if (!runsApiAvailable.value) return
  runsLoading.value = true
  try {
    const response = await automationApi.runs({ page, pageSize: runsPageSize })
    runs.value = response.items
    runsTotal.value = response.total
    runsPage.value = response.page
  } catch (caught) {
    error.value = message(caught, '无法读取自动化执行记录')
  } finally {
    runsLoading.value = false
  }
}

function changeRunsPage(page: number): void {
  void loadRuns(page)
}

async function finishRun(run: AutomationRun): Promise<void> {
  stopPolling()
  currentRun.value = run
  if (run.state === 'FAILED') {
    feedback.value = null
    error.value = run.error_message ?? runSummary(run)
  } else {
    error.value = null
    feedback.value = runSummary(run)
  }
  try {
    await refreshJobs()
    await loadRuns(1)
  } catch (caught) {
    error.value = message(caught, '自动搜索已结束，但无法刷新任务记录')
  }
}

function schedulePoll(runId: string, generation: number): void {
  if (!isMounted || generation !== pollGeneration) return
  pollTimer = globalThis.setTimeout(() => {
    void pollRun(runId, generation)
  }, pollIntervalMs)
}

async function pollRun(runId: string, generation: number): Promise<void> {
  if (!isMounted || generation !== pollGeneration) return
  pollTimer = undefined
  try {
    const run = await automationApi.runStatus(runId)
    if (!isMounted || generation !== pollGeneration) return
    error.value = null
    currentRun.value = run
    if (isActive(run)) schedulePoll(runId, generation)
    else await finishRun(run)
  } catch (caught) {
    if (!isMounted || generation !== pollGeneration) return
    error.value = message(caught, '无法读取自动搜索进度，将继续重试')
    schedulePoll(runId, generation)
  }
}

function startPolling(run: AutomationRun): void {
  stopPolling()
  const generation = pollGeneration
  schedulePoll(run.id, generation)
}

function startPollingNow(runId: string): void {
  stopPolling()
  const generation = pollGeneration
  void pollRun(runId, generation)
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const [policy, page, latestRun] = await Promise.all([
      automationApi.policy(),
      automationApi.jobs(),
      automationApi.latestRun(),
    ])
    applyPolicy(policy)
    jobs.value = page.items
    currentRun.value = latestRun
    if (latestRun && isActive(latestRun)) startPolling(latestRun)
    await Promise.all([loadMediaOptions(), loadRuns(1)])
  } catch (caught) {
    error.value = message(caught, '无法读取自动化配置')
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  error.value = null
  feedback.value = null
  try {
    const maxSize = form.max_size_gib ? Number(form.max_size_gib) * 1024 ** 3 : null
    const dailyBytes = form.daily_download_gib
      ? Number(form.daily_download_gib) * 1024 ** 3
      : null
    const policy = await automationApi.updatePolicy({
      enabled: form.enabled,
      dry_run: form.dry_run,
      auto_identify: form.auto_identify,
      scope_mode: form.scope_mode,
      regions: form.regions,
      selected_media_ids: form.selected_media_ids,
      site_ids: form.site_ids,
      media_types: form.media_types,
      minimum_score: form.minimum_score,
      minimum_seeders: form.minimum_seeders,
      max_size_bytes: maxSize ? Math.round(maxSize) : null,
      allow_warnings: form.allow_warnings,
      interval_minutes: form.interval_minutes,
      retry_delay_minutes: form.retry_delay_minutes,
      max_attempts: form.max_attempts,
      daily_download_limit: form.daily_download_limit,
      daily_download_bytes: dailyBytes ? Math.round(dailyBytes) : null,
    })
    applyPolicy(policy)
    feedback.value = '策略已保存'
  } catch (caught) {
    error.value = message(caught, '无法保存自动化策略')
  } finally {
    saving.value = false
  }
}

async function run(): Promise<void> {
  if (starting.value || runIsActive.value) return
  starting.value = true
  error.value = null
  feedback.value = null
  try {
    const createdRun = await automationApi.run()
    currentRun.value = createdRun
    void loadRuns(1)
    if (isActive(createdRun)) {
      feedback.value = '自动搜索已在后台开始，可离开页面后再回来查看进度。'
      startPolling(createdRun)
    } else {
      await finishRun(createdRun)
    }
  } catch (caught) {
    error.value = message(caught, '无法启动自动搜索')
  } finally {
    starting.value = false
  }
}

async function retry(jobId: string): Promise<void> {
  error.value = null
  try {
    const retryJob = await automationApi.retry(jobId)
    feedback.value = '失败任务已立即开始重试'
    startPollingNow(retryJob.run_id)
    try {
      await Promise.all([refreshJobs(), loadRuns(1)])
    } catch (caught) {
      error.value = message(caught, '重试已启动，但任务列表暂时无法刷新')
    }
  } catch (caught) {
    error.value = message(caught, '无法重新执行任务')
  }
}

onMounted(() => {
  isMounted = true
  void load()
})

onBeforeUnmount(() => {
  isMounted = false
  stopPolling()
})
</script>

<template>
  <section class="page automation-page">
    <PageHeader
      eyebrow="AUTOMATION"
      title="自动搜索"
      :description="form.dry_run
        ? '按策略周期搜索缺失影视并解释候选选择；当前为试运行，不会提交下载。'
        : '按策略周期搜索并自动提交满足条件的候选；受 qB 写入开关和每日预算限制。'"
    >
      <button class="button primary" :disabled="starting || runIsActive || !form.enabled" @click="run">
        {{ starting ? '启动中…' : (runIsActive ? '后台执行中…' : (form.dry_run ? '立即试运行' : '立即运行')) }}
      </button>
    </PageHeader>

    <PageState :loading="loading" :error="error" />
    <p v-if="feedback" class="configuration-message success">{{ feedback }}</p>

    <article v-if="currentRun" class="panel library-filters automation-run-progress" aria-live="polite">
      <div class="section-heading">
        <div>
          <span class="eyebrow">CURRENT RUN</span>
          <h2>后台自动搜索</h2>
        </div>
        <div class="automation-current-actions">
          <a class="button secondary small" :href="`/automation/runs/${currentRun.id}`">查看任务</a>
          <StatusPill :status="currentRun.state" />
        </div>
      </div>
      <p class="muted">
        {{ currentRun.trigger === 'scheduled' ? '周期任务' : '手动运行' }} ·
        {{ formatShanghai(currentRun.started_at ?? currentRun.created_at) }}
      </p>
      <p>
        已完成 {{ runCompleted }} / {{ currentRun.created }} 项 · 成功 {{ currentRun.succeeded }} 项 ·
        失败 {{ currentRun.failed }} 项 · 等待后续 {{ currentRun.deferred }} 项
      </p>
      <div class="progress-track" :aria-label="`自动搜索进度 ${runProgress}%`">
        <span :style="{ width: `${runProgress}%` }"></span>
      </div>
      <p v-if="runIsActive && currentRun.created === 0" class="muted">正在准备搜索任务…</p>
      <p v-if="currentRun.error_message" class="inline-warning">{{ currentRun.error_message }}</p>
    </article>

    <form v-if="!loading" class="panel" aria-labelledby="automation-policy-title" @submit.prevent="save">
      <div class="section-heading">
        <div><span class="eyebrow">POLICY</span><h2 id="automation-policy-title">搜索策略</h2></div>
        <StatusPill :status="form.enabled ? 'READY' : 'PAUSED'" :label="form.enabled ? '已启用' : '已停用'" />
      </div>
      <div class="configuration-field-grid">
        <label class="configuration-checkbox">
          <input v-model="form.enabled" name="enabled" type="checkbox" /> 启用自动化策略
        </label>
        <label class="configuration-checkbox">
          <input v-model="form.dry_run" name="dry_run" type="checkbox" /> 仅试运行，不提交下载
        </label>
        <label class="configuration-checkbox">
          <input v-model="form.auto_identify" name="auto_identify" type="checkbox" /> 自动识别缺少 TMDB ID 的影视
        </label>
        <label>
          自动化范围
          <select v-model="form.scope_mode" name="scope_mode">
            <option value="filters">按类型和地区规则</option>
            <option value="selected">只处理手动选择的影视</option>
          </select>
        </label>
        <fieldset class="configuration-checkbox">
          <legend>影视类型</legend>
          <label><input v-model="form.media_types" type="checkbox" value="movie" /> 电影</label>
          <label><input v-model="form.media_types" type="checkbox" value="tv" /> 电视剧</label>
        </fieldset>
        <label>
          最低评分
          <input v-model.number="form.minimum_score" type="number" min="0" max="1" step="0.05" />
        </label>
        <label>
          最低做种数
          <input v-model.number="form.minimum_seeders" type="number" min="0" step="1" />
        </label>
        <label>
          最大体积（GiB，留空不限）
          <input v-model="form.max_size_gib" type="number" min="0.1" step="0.1" />
        </label>
        <label class="configuration-checkbox">
          <input v-model="form.allow_warnings" type="checkbox" /> 允许选择带风险提示的候选
        </label>
        <label>
          执行间隔（分钟）
          <input v-model.number="form.interval_minutes" name="interval_minutes" type="number" min="5" max="1440" />
        </label>
        <label>
          首次重试延迟（分钟）
          <input v-model.number="form.retry_delay_minutes" type="number" min="1" max="1440" />
        </label>
        <label>
          最大尝试次数
          <input v-model.number="form.max_attempts" type="number" min="1" max="10" />
        </label>
        <label>
          每日自动下载数量
          <input v-model.number="form.daily_download_limit" name="daily_download_limit" type="number" min="1" max="100" />
        </label>
        <label>
          每日自动下载体积（GiB，留空不限）
          <input v-model="form.daily_download_gib" type="number" min="0.1" step="0.1" />
        </label>
      </div>
      <div v-if="form.scope_mode === 'filters'" class="filter-heading">
        <div>
          <span class="eyebrow">REGIONS</span>
          <strong>地区范围</strong>
          <p class="muted">不选择地区时处理全部地区；可同时选择多个地区。</p>
        </div>
        <div class="configuration-field-grid">
          <label v-for="region in regionOptions" :key="region" class="configuration-checkbox">
            <input v-model="form.regions" type="checkbox" :value="region" /> {{ region }}
          </label>
        </div>
      </div>
      <div v-else class="automation-media-picker">
        <div class="filter-heading">
          <div>
            <span class="eyebrow">MANUAL SCOPE</span>
            <strong>手动选择影视</strong>
          </div>
          <span class="muted">已选择 {{ form.selected_media_ids.length }} 项</span>
        </div>
        <div class="filter-grid">
          <label>
            关键词
            <input v-model="mediaQuery.query" type="search" placeholder="中文名、原名" />
          </label>
          <label>
            类型
            <select v-model="mediaQuery.mediaType">
              <option value="">全部类型</option>
              <option value="movie">电影</option>
              <option value="tv">电视剧</option>
            </select>
          </label>
          <label>
            地区
            <select v-model="mediaQuery.region">
              <option value="">全部地区</option>
              <option v-for="region in regionOptions" :key="region" :value="region">{{ region }}</option>
            </select>
          </label>
          <button class="button secondary" type="button" :disabled="mediaOptionsLoading" @click="loadMediaOptions(1)">
            {{ mediaOptionsLoading ? '查询中…' : '查询影视' }}
          </button>
        </div>
        <p class="muted">当前查询 {{ mediaOptionsTotal }} 项；每页显示 {{ mediaOptionsPageSize }} 项，可用关键词继续缩小范围。</p>
        <div class="download-stack automation-media-options">
          <label v-for="item in mediaOptions" :key="item.id" class="download-card configuration-checkbox">
            <input
              type="checkbox"
              :checked="form.selected_media_ids.includes(item.id)"
              @change="toggleMedia(item.id)"
            />
            <span>
              <strong>{{ item.title }}</strong>
              <small class="muted">
                {{ item.media_type === 'tv' ? '电视剧' : '电影' }} · {{ item.year ?? '年份未知' }} ·
                {{ item.regions.length ? item.regions.join(' / ') : '地区未知' }}
              </small>
            </span>
          </label>
        </div>
        <Pagination
          :page="mediaOptionsPage"
          :total="mediaOptionsTotal"
          :page-size="mediaOptionsPageSize"
          label="影视选择分页"
          @change="changeMediaOptionsPage"
        />
      </div>
      <p class="muted">
        当前站点：AvistaZ；媒体类型：电影和电视剧。真实下载还要求部署环境启用 ENABLE_QB_WRITE。
      </p>
      <div class="filter-actions">
        <button class="button primary" type="submit" :disabled="saving">
          {{ saving ? '保存中…' : '保存策略' }}
        </button>
      </div>
    </form>

    <div v-if="!loading" class="panel automation-history">
      <div class="filter-heading">
        <div><span class="eyebrow">RUN HISTORY</span><strong>自动化执行记录</strong></div>
        <span class="muted">{{ runsApiAvailable ? `${runsTotal} 次执行` : `${jobs.length} 条任务` }}</span>
      </div>

      <template v-if="runsApiAvailable">
        <p v-if="runsLoading" class="muted">正在读取执行记录…</p>
        <p v-else-if="runs.length === 0" class="muted">尚未执行自动搜索。</p>
        <div v-else class="automation-run-list">
          <article v-for="(runItem, index) in runs" :key="runItem.id" class="automation-run-card">
            <div class="automation-run-card-main">
              <div>
                <span class="eyebrow">第 {{ (runsPage - 1) * runsPageSize + index + 1 }} 次执行</span>
                <h3>{{ runItem.trigger === 'scheduled' ? '周期自动搜索' : '手动自动搜索' }}</h3>
                <p class="muted">{{ formatShanghai(runItem.created_at) }}</p>
              </div>
              <StatusPill :status="runItem.state" />
            </div>
            <div class="automation-run-counts">
              <strong>共 {{ runItem.created }} 项</strong>
              <span class="success-text">成功 {{ runItem.succeeded }} 项</span>
              <span v-if="runItem.failed" class="danger-text">失败 {{ runItem.failed }} 项</span>
              <span v-if="runItem.deferred" class="warning-text">等待 {{ runItem.deferred }} 项</span>
            </div>
            <p v-if="runItem.error_message" class="inline-warning">{{ runItem.error_message }}</p>
            <a class="button secondary small" :href="`/automation/runs/${runItem.id}`">
              查看任务详情
            </a>
          </article>
        </div>
        <Pagination
          :page="runsPage"
          :total="runsTotal"
          :page-size="runsPageSize"
          label="自动化执行记录分页"
          @change="changeRunsPage"
        />
      </template>

      <!-- Compatibility fallback while an older API is being upgraded. -->
      <template v-else>
        <p v-if="jobs.length === 0" class="muted">尚未执行自动搜索。</p>
        <div v-else class="download-stack">
          <article v-for="job in jobs" :key="job.id" class="download-card">
            <div>
              <strong>{{ job.media_title }}</strong>
              <p class="muted">{{ formatShanghai(job.created_at) }} · 候选 {{ job.decision.candidate_count ?? 0 }} 个</p>
              <p v-if="job.error_message" class="inline-warning">{{ job.error_message }}</p>
              <button v-if="(job.state === 'FAILED' || job.state === 'RETRY_WAIT') && !job.superseded_at" class="button secondary small" type="button" @click="retry(job.id)">
                重新执行
              </button>
            </div>
            <StatusPill :status="job.superseded_at ? 'SUPERSEDED' : job.state" />
          </article>
        </div>
      </template>
    </div>
  </section>
</template>
