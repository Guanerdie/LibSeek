<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError, automationApi } from '../api/client'
import AutomationRunOutcomes from '../components/AutomationRunOutcomes.vue'
import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import Pagination from '../components/Pagination.vue'
import StatusPill from '../components/StatusPill.vue'
import type { AutomationJob, AutomationRun } from '../types'
import { automationJobLabel } from '../utils/automation'
import { formatShanghai, statusLabel } from '../utils/format'

const route = useRoute()
const router = useRouter()
const runId = computed(() => String(route.params.runId))
const run = ref<AutomationRun | null>(null)
const jobs = ref<AutomationJob[]>([])
const jobsTotal = ref(0)
const jobsPage = ref(1)
const jobsPageSize = 10
const loading = ref(true)
const jobsLoading = ref(false)
const error = ref<string | null>(null)
const feedback = ref<string | null>(null)
const retryingJobId = ref<string | null>(null)

const pollIntervalMs = 2_000
let pollTimer: ReturnType<typeof globalThis.setTimeout> | undefined
let mounted = false
let waitingForRetryRun = false

const active = computed(() => run.value?.state === 'PENDING' || run.value?.state === 'RUNNING')
const completed = computed(() => {
  const item = run.value
  return item ? item.succeeded + item.failed + item.deferred : 0
})
const progress = computed(() => {
  const item = run.value
  if (!item || item.created === 0) return active.value ? 0 : 100
  return Math.min(100, Math.round((completed.value / item.created) * 100))
})

function message(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback
}

function stopPolling(): void {
  if (pollTimer !== undefined) {
    globalThis.clearTimeout(pollTimer)
    pollTimer = undefined
  }
}

function schedulePoll(): void {
  if (!mounted || (!active.value && !waitingForRetryRun)) return
  pollTimer = globalThis.setTimeout(() => void poll(), pollIntervalMs)
}

async function loadJobs(page = jobsPage.value): Promise<void> {
  jobsLoading.value = true
  try {
    const response = await automationApi.runJobs(runId.value, {
      page,
      pageSize: jobsPageSize,
    })
    jobs.value = response.items
    jobsTotal.value = response.total
    jobsPage.value = response.page
  } catch (caught) {
    error.value = message(caught, '无法读取本次执行的任务')
  } finally {
    jobsLoading.value = false
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const [runResponse] = await Promise.all([
      automationApi.runStatus(runId.value),
      loadJobs(1),
    ])
    run.value = runResponse
    waitingForRetryRun = false
    if (active.value) schedulePoll()
  } catch (caught) {
    error.value = message(caught, '无法读取自动化执行详情')
    if (waitingForRetryRun) schedulePoll()
  } finally {
    loading.value = false
  }
}

async function poll(): Promise<void> {
  if (!mounted || (!active.value && !waitingForRetryRun)) return
  pollTimer = undefined
  try {
    run.value = await automationApi.runStatus(runId.value)
    waitingForRetryRun = false
    error.value = null
    await loadJobs(jobsPage.value)
    if (active.value) schedulePoll()
  } catch (caught) {
    if (!mounted) return
    error.value = message(caught, '无法读取执行进度，将继续重试')
    schedulePoll()
  }
}

function changeJobsPage(page: number): void {
  void loadJobs(page)
}

async function retry(job: AutomationJob): Promise<void> {
  if (retryingJobId.value) return
  retryingJobId.value = job.id
  error.value = null
  feedback.value = null
  try {
    waitingForRetryRun = true
    const updated = await automationApi.retry(job.id)
    await router.push(`/automation/runs/${updated.run_id}`)
    feedback.value = '失败任务已立即开始重试'
  } catch (caught) {
    waitingForRetryRun = false
    error.value = message(caught, '无法重新执行任务')
  } finally {
    retryingJobId.value = null
  }
}

function rejectedReasons(job: AutomationJob): string[] {
  return (job.decision.rejected ?? []).flatMap((item) => item.reasons ?? []).slice(0, 4)
}

function manualScreeningHref(job: AutomationJob): string {
  const base = `/library/${encodeURIComponent(job.media_id)}/resources`
  return job.search_id ? `${base}?search=${encodeURIComponent(job.search_id)}` : base
}

onMounted(() => {
  mounted = true
  void load()
})

watch(runId, () => {
  stopPolling()
  run.value = null
  jobs.value = []
  jobsTotal.value = 0
  jobsPage.value = 1
  feedback.value = null
  void load()
})

onBeforeUnmount(() => {
  mounted = false
  stopPolling()
})
</script>

<template>
  <section class="page automation-detail-page">
    <PageHeader
      eyebrow="AUTOMATION RUN"
      :title="run ? (run.trigger === 'scheduled' ? '周期自动搜索详情' : '手动自动搜索详情') : '自动化执行详情'"
      description="按任务查看本次执行结果；失败任务可以重试，也可以进入资源页人工筛选。"
    >
      <RouterLink class="button secondary" to="/automation">返回执行记录</RouterLink>
    </PageHeader>

    <PageState :loading="loading" :error="error" />

    <template v-if="!loading && run">
      <article class="panel automation-detail-summary">
        <div class="section-heading">
          <div>
            <span class="eyebrow">{{ formatShanghai(run.created_at) }}</span>
            <h2>{{ run.state === 'FAILED' ? '本次执行失败' : (active ? '本次执行进行中' : '本次执行完成') }}</h2>
          </div>
          <StatusPill :status="run.state" :label="statusLabel(run.state)" />
        </div>
        <div class="automation-run-counts">
          <strong>任务 {{ run.created }} 项</strong>
          <AutomationRunOutcomes :run="run" />
          <span class="muted">已完成 {{ completed }} / {{ run.created }} 项</span>
        </div>
        <p v-if="run.outcomes?.existing_download || run.outcomes?.awaiting_library" class="muted">搜索前跳过的已有下载不占搜索名额；下载完成仍缺失的影视需核查入库。</p>
        <div class="progress-track" :aria-label="`自动搜索进度 ${progress}%`"><span :style="{ width: `${progress}%` }"></span></div>
        <p v-if="run.error_message" class="inline-warning">{{ run.error_message }}</p>
      </article>

      <article class="panel automation-job-panel">
        <div class="filter-heading">
          <div><span class="eyebrow">TASKS</span><strong>任务明细</strong></div>
          <span class="muted">共 {{ jobsTotal }} 项</span>
        </div>
        <PageState :loading="jobsLoading" :empty="!jobsLoading && jobs.length === 0" empty-text="本次执行尚未生成任务" />
        <div v-if="jobs.length" class="automation-job-list">
          <article v-for="(job, index) in jobs" :key="job.id" class="automation-job-card">
            <div class="automation-job-heading">
              <div>
                <span class="eyebrow">任务 {{ (jobsPage - 1) * jobsPageSize + index + 1 }}</span>
                <h3>{{ job.media_title }}</h3>
                <p class="muted">{{ formatShanghai(job.created_at) }} · 尝试 {{ job.attempt_count }} 次</p>
              </div>
              <StatusPill
                :status="job.superseded_at ? 'SUPERSEDED' : job.state"
                :label="automationJobLabel(job)"
              />
            </div>
            <div class="automation-job-meta">
              <span v-if="job.decision.candidate_count !== undefined">候选 {{ job.decision.candidate_count }} 个</span>
              <span v-if="job.decision.selected_title" class="success-text">已选择：{{ job.decision.selected_title }}</span>
              <span v-if="job.decision.download_skipped" class="warning-text">{{ job.decision.download_skipped }}</span>
            </div>
            <p v-if="job.error_message" class="inline-warning">{{ job.error_message }}</p>
            <p v-if="job.outcome === 'no_match'" class="muted">没有候选满足当前策略</p>
            <p v-if="job.outcome === 'awaiting_library'" class="muted">下载已完成，NextFind 仍报告缺失，请核查入库。</p>
            <p v-if="job.state === 'SUCCEEDED' && (!job.outcome || job.outcome === 'unknown')" class="muted">历史记录不足，无法确认本次是否新增下载。</p>
            <ul v-if="rejectedReasons(job).length" class="reason-list">
              <li v-for="reason in rejectedReasons(job)" :key="reason">{{ reason }}</li>
            </ul>
            <div class="automation-job-actions">
              <button
                v-if="(job.state === 'FAILED' || job.state === 'RETRY_WAIT') && !job.superseded_at"
                class="button secondary small"
                type="button"
                :disabled="retryingJobId !== null"
                @click="retry(job)"
              >
                {{ retryingJobId === job.id ? '重试中…' : (job.state === 'RETRY_WAIT' ? '立即重试' : '重试任务') }}
              </button>
              <RouterLink
                v-if="job.state === 'FAILED' || job.search_id"
                class="button primary small"
                :to="manualScreeningHref(job)"
              >
                人工筛选资源
              </RouterLink>
            </div>
          </article>
        </div>
        <Pagination
          :page="jobsPage"
          :total="jobsTotal"
          :page-size="jobsPageSize"
          label="自动化任务分页"
          @change="changeJobsPage"
        />
      </article>
      <p v-if="feedback" class="configuration-message success">{{ feedback }}</p>
    </template>
  </section>
</template>
