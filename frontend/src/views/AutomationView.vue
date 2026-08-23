<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { ApiError, automationApi } from '../api/client'
import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import type { AutomationJob, AutomationPolicy, AutomationRunResult } from '../types'
import { formatShanghai } from '../utils/format'

const form = reactive({
  enabled: false,
  dry_run: true,
  auto_identify: true,
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
const loading = ref(true)
const saving = ref(false)
const running = ref(false)
const error = ref<string | null>(null)
const feedback = ref<string | null>(null)

function applyPolicy(policy: AutomationPolicy): void {
  form.enabled = policy.enabled
  form.dry_run = policy.dry_run
  form.auto_identify = policy.auto_identify
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

function message(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const [policy, page] = await Promise.all([automationApi.policy(), automationApi.jobs()])
    applyPolicy(policy)
    jobs.value = page.items
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
  running.value = true
  error.value = null
  feedback.value = null
  try {
    const result: AutomationRunResult = await automationApi.run()
    const deferred = result.created - result.succeeded - result.failed
    const summary = `${form.dry_run ? '试运行' : '运行'}完成：处理 ${result.created} 项，成功 ${result.succeeded} 项，失败 ${result.failed} 项`
    feedback.value = deferred > 0 ? `${summary}，等待后续执行 ${deferred} 项` : summary
    jobs.value = (await automationApi.jobs()).items
  } catch (caught) {
    error.value = message(caught, '无法执行自动搜索试运行')
  } finally {
    running.value = false
  }
}

async function retry(jobId: string): Promise<void> {
  error.value = null
  try {
    await automationApi.retry(jobId)
    jobs.value = (await automationApi.jobs()).items
    feedback.value = '失败任务已进入重试队列'
  } catch (caught) {
    error.value = message(caught, '无法重新执行任务')
  }
}

onMounted(load)
</script>

<template>
  <section class="page">
    <PageHeader
      eyebrow="AUTOMATION"
      title="自动搜索"
      :description="form.dry_run
        ? '按策略周期搜索缺失影视并解释候选选择；当前为试运行，不会提交下载。'
        : '按策略周期搜索并自动提交满足条件的候选；受 qB 写入开关和每日预算限制。'"
    >
      <button class="button primary" :disabled="running || !form.enabled" @click="run">
        {{ running ? '执行中…' : (form.dry_run ? '立即试运行' : '立即运行') }}
      </button>
    </PageHeader>

    <PageState :loading="loading" :error="error" />
    <p v-if="feedback" class="configuration-message success">{{ feedback }}</p>

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
      <p class="muted">
        当前站点：AvistaZ；媒体类型：电影和电视剧。真实下载还要求部署环境启用 ENABLE_QB_WRITE。
      </p>
      <div class="filter-actions">
        <button class="button primary" type="submit" :disabled="saving">
          {{ saving ? '保存中…' : '保存策略' }}
        </button>
      </div>
    </form>

    <div v-if="!loading" class="panel">
      <div class="filter-heading">
        <div><span class="eyebrow">RUN HISTORY</span><strong>最近试运行</strong></div>
        <span class="muted">{{ jobs.length }} 条记录</span>
      </div>
      <p v-if="jobs.length === 0" class="muted">尚未执行自动搜索。</p>
      <div v-else class="download-stack">
        <article v-for="job in jobs" :key="job.id" class="download-card">
          <div>
            <strong>{{ job.media_title }}</strong>
            <p class="muted">{{ formatShanghai(job.created_at) }} · 候选 {{ job.decision.candidate_count ?? 0 }} 个</p>
            <p class="muted">
              {{ job.trigger === 'scheduled' ? '周期任务' : '手动运行' }} · 已尝试 {{ job.attempt_count }} 次
              <span v-if="job.next_attempt_at"> · 下次 {{ formatShanghai(job.next_attempt_at) }}</span>
            </p>
            <p v-if="job.decision.selected_title" class="configuration-message success">
              试运行选择：{{ job.decision.selected_title }}（{{ Math.round((job.decision.selected_score ?? 0) * 100) }} 分）
            </p>
            <p v-else-if="job.state === 'SUCCEEDED'" class="inline-warning">没有候选满足当前策略</p>
            <p v-if="job.decision.download_skipped" class="inline-warning">
              {{ job.decision.download_skipped }}
            </p>
            <p v-if="job.error_message" class="inline-warning">{{ job.error_message }}</p>
            <button
              v-if="job.state === 'FAILED'"
              class="button secondary small"
              type="button"
              @click="retry(job.id)"
            >
              重新执行
            </button>
          </div>
          <StatusPill :status="job.state" />
        </article>
      </div>
    </div>
  </section>
</template>
