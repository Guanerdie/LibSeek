<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError, dailyApi } from '../api/client'
import PageHeader from '../components/PageHeader.vue'
import type {
  ImportBatch,
  ImportBatchItem,
  ImportMatchLine,
  ImportMediaChoice,
  ImportOutcome,
} from '../types'
import { formatShanghai } from '../utils/format'

const text = ref('')
const lines = ref<ImportMatchLine[] | null>(null)
const truncated = ref(false)
// For a line with several candidates: which one the operator picked, by index.
const picked = reactive<Record<number, string>>({})
const matching = ref(false)
const starting = ref(false)
const error = ref<string | null>(null)
const batch = ref<ImportBatch | null>(null)

const pollIntervalMs = 2_000
let pollTimer: ReturnType<typeof globalThis.setTimeout> | undefined
let mounted = false

function message(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback
}

function describe(media: ImportMediaChoice): string {
  const kind = media.media_type === 'tv' ? '电视剧' : '电影'
  return `${media.title}（${media.year ?? '年份未知'} · ${kind}）`
}

const statusText: Record<ImportMatchLine['status'], string> = {
  MATCHED: '将下载',
  AMBIGUOUS: '需要你选择',
  NOT_MISSING: '不在缺失列表',
  DOWNLOADING: '已在下载',
  NOT_FOUND: '没有找到',
  DUPLICATE: '重复',
}

const outcomeText: Record<ImportOutcome, string> = {
  PENDING: '等待中',
  RUNNING: '搜索中…',
  DOWNLOADED: '已提交下载',
  NO_CANDIDATE: '没有合格资源',
  FAILED: '失败',
  CANCELLED: '已取消',
}

function outcomeClass(outcome: ImportOutcome): string {
  if (outcome === 'DOWNLOADED') return 'state-succeeded'
  if (outcome === 'RUNNING') return 'state-running'
  if (outcome === 'FAILED') return 'state-failed'
  return ''
}

/** The titles that would be downloaded, each once, in the order pasted. */
const chosenIds = computed(() => {
  const ids: string[] = []
  ;(lines.value ?? []).forEach((line, index) => {
    const id = line.status === 'MATCHED' ? line.media?.media_id : picked[index]
    if (id && (line.status === 'MATCHED' || line.status === 'AMBIGUOUS') && !ids.includes(id)) {
      ids.push(id)
    }
  })
  return ids
})

const counts = computed(() => {
  const all = lines.value ?? []
  const undecided = all.filter(
    (line, index) => line.status === 'AMBIGUOUS' && !picked[index],
  ).length
  return {
    ready: chosenIds.value.length,
    undecided,
    skipped: all.filter(
      (line) => line.status !== 'MATCHED' && line.status !== 'AMBIGUOUS',
    ).length,
  }
})

const running = computed(() => batch.value?.state === 'RUNNING')
const tally = computed(() => {
  const items = batch.value?.items ?? []
  const count = (outcome: ImportOutcome) => items.filter((item) => item.outcome === outcome).length
  return {
    total: items.length,
    done: items.filter((item) => item.outcome !== 'PENDING' && item.outcome !== 'RUNNING').length,
    downloaded: count('DOWNLOADED'),
    none: count('NO_CANDIDATE'),
    failed: count('FAILED'),
    cancelled: count('CANCELLED'),
  }
})

async function match(): Promise<void> {
  if (!text.value.trim() || matching.value) return
  matching.value = true
  error.value = null
  try {
    const result = await dailyApi.importMatch(text.value)
    lines.value = result.lines
    truncated.value = result.truncated
    for (const key of Object.keys(picked)) delete picked[Number(key)]
    // Nothing is chosen on the operator's behalf where a line was unclear.
    result.lines.forEach((line, index) => {
      if (line.status === 'AMBIGUOUS') picked[index] = ''
    })
  } catch (caught) {
    error.value = message(caught, '无法匹配这份清单')
  } finally {
    matching.value = false
  }
}

function stopPolling(): void {
  if (pollTimer !== undefined) {
    globalThis.clearTimeout(pollTimer)
    pollTimer = undefined
  }
}

function schedulePoll(): void {
  stopPolling()
  if (!mounted || !running.value) return
  pollTimer = globalThis.setTimeout(() => void poll(), pollIntervalMs)
}

async function poll(): Promise<void> {
  if (!mounted) return
  try {
    batch.value = await dailyApi.importStatus()
  } catch (caught) {
    error.value = message(caught, '无法读取下载进度，将继续重试')
  }
  schedulePoll()
}

async function start(): Promise<void> {
  if (!chosenIds.value.length || starting.value || running.value) return
  starting.value = true
  error.value = null
  try {
    batch.value = await dailyApi.importStart(chosenIds.value)
    lines.value = null
    text.value = ''
    schedulePoll()
  } catch (caught) {
    error.value = message(caught, '无法开始下载')
  } finally {
    starting.value = false
  }
}

async function cancel(): Promise<void> {
  try {
    batch.value = await dailyApi.importCancel()
  } catch (caught) {
    error.value = message(caught, '无法停止')
  }
}

function itemNote(item: ImportBatchItem): string | null {
  if (item.outcome === 'DOWNLOADED') return item.selected_title
  return item.message
}

onMounted(async () => {
  mounted = true
  try {
    const current = await dailyApi.importStatus()
    // A batch started earlier is still worth showing: it may be running, or
    // its results may not have been read yet.
    if (current.state !== 'IDLE') batch.value = current
    schedulePoll()
  } catch {
    // The page is usable without the previous batch.
  }
})

onBeforeUnmount(() => {
  mounted = false
  stopPolling()
})
</script>

<template>
  <section class="page import-page">
    <PageHeader
      eyebrow="IMPORT"
      title="批量导入"
      description="贴一份急需入库的清单，系统逐部搜索并按选种标准自动下载最好的资源。"
    >
      <RouterLink class="button secondary" to="/library">返回缺失列表</RouterLink>
    </PageHeader>

    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>

    <article v-if="batch" class="panel import-panel" aria-live="polite">
      <div class="section-heading">
        <div>
          <h2>{{ running ? '正在下载这一批' : '上一批的结果' }}</h2>
          <p class="muted">
            {{ formatShanghai(batch.started_at) }} 由 {{ batch.started_by ?? '未知' }} 发起 ·
            已处理 {{ tally.done }} / {{ tally.total }} 部
          </p>
        </div>
        <button v-if="running" type="button" class="button secondary small" :disabled="batch.cancel_requested" @click="cancel">
          {{ batch.cancel_requested ? '处理完当前这部后停止…' : '停止' }}
        </button>
      </div>
      <div class="progress-track" :aria-label="`批量下载进度 ${tally.done} / ${tally.total}`">
        <span :style="{ width: `${tally.total ? Math.round((tally.done / tally.total) * 100) : 0}%` }"></span>
      </div>
      <p class="import-tally">
        <span class="success-text">已提交下载 {{ tally.downloaded }} 部</span>
        <span v-if="tally.none" class="warning-text">没有合格资源 {{ tally.none }} 部</span>
        <span v-if="tally.failed" class="danger-text">失败 {{ tally.failed }} 部</span>
        <span v-if="tally.cancelled" class="muted">已取消 {{ tally.cancelled }} 部</span>
      </p>
      <ul class="cleanup-list">
        <li v-for="item in batch.items" :key="item.media_id">
          <span>
            <strong>{{ item.title }}</strong>
            <span v-if="itemNote(item)" class="muted">{{ itemNote(item) }}</span>
          </span>
          <span class="status-pill" :class="outcomeClass(item.outcome)">{{ outcomeText[item.outcome] }}</span>
        </li>
      </ul>
      <p v-if="!running" class="muted">
        下载进度在<RouterLink to="/downloads">「下载」页</RouterLink>，每一步的依据在<RouterLink to="/activity">「记录」页</RouterLink>。
        没有合格资源的可以到影视详情页人工筛选。
      </p>
    </article>

    <form class="panel import-panel" @submit.prevent="match">
      <div class="section-heading">
        <div>
          <h2>1. 贴上清单</h2>
          <p class="muted">
            每行一部，可以混着写：片名、片名加年份、TMDB 编号、TMDB 链接。只会下载 NextFind 缺失列表里的影视，一次最多 200 行。
          </p>
        </div>
      </div>
      <textarea
        v-model="text"
        name="import_text"
        class="import-text"
        rows="8"
        spellcheck="false"
        placeholder="红宝石戒指 (2013)&#10;少年星球&#10;https://www.themoviedb.org/tv/12345&#10;tmdb:67890"
      ></textarea>
      <div class="filter-actions">
        <button class="button primary" type="submit" :disabled="matching || !text.trim()">
          {{ matching ? '匹配中…' : '匹配' }}
        </button>
      </div>
    </form>

    <article v-if="lines" class="panel import-panel">
      <div class="section-heading">
        <div>
          <h2>2. 核对后开始</h2>
          <p class="muted">
            {{ counts.ready }} 部将下载<template v-if="counts.undecided">，{{ counts.undecided }} 部需要你选择</template><template v-if="counts.skipped">，{{ counts.skipped }} 部不会下载</template>。
            不占每日下载额度，仍按最低评分、做种数和体积上限选种。
          </p>
        </div>
      </div>
      <p v-if="truncated" class="inline-warning">清单超过 200 行，只匹配了前 200 行。剩下的请分批导入。</p>
      <p v-if="!lines.length" class="muted cleanup-empty">清单里没有可识别的内容。</p>
      <ul v-else class="cleanup-list import-lines">
        <li v-for="(line, index) in lines" :key="index" :class="`import-${line.status.toLowerCase()}`">
          <span>
            <strong>{{ line.raw }}</strong>
            <span v-if="line.status === 'AMBIGUOUS'" class="muted">
              {{ line.note ?? '有多部影视叫这个名字' }}
              <select v-model="picked[index]" :aria-label="`为「${line.raw}」选择影视`">
                <option value="">不下载这一行</option>
                <option v-for="choice in line.choices" :key="choice.media_id" :value="choice.media_id">
                  {{ describe(choice) }}
                </option>
              </select>
            </span>
            <span v-else-if="line.media" class="muted">
              {{ describe(line.media) }}<template v-if="line.note"> · {{ line.note }}</template>
            </span>
            <span v-else class="muted">缺失列表里没有这部。可能已经入库，或者 NextFind 还没有收录。</span>
          </span>
          <span
            class="status-pill"
            :class="{
              'state-succeeded': line.status === 'MATCHED' || (line.status === 'AMBIGUOUS' && picked[index]),
              'state-paused': line.status === 'AMBIGUOUS' && !picked[index],
            }"
          >
            {{ line.status === 'AMBIGUOUS' && picked[index] ? '将下载' : statusText[line.status] }}
          </span>
        </li>
      </ul>
      <div class="filter-actions import-start">
        <span v-if="running" class="muted">上一批还在下载，结束后才能开始新的一批。</span>
        <button
          class="button primary"
          type="button"
          :disabled="!counts.ready || starting || running"
          @click="start"
        >
          {{ starting ? '启动中…' : `开始下载 ${counts.ready} 部` }}
        </button>
      </div>
    </article>
  </section>
</template>
