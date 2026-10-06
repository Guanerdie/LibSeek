<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { activityApi, ApiError, automationApi, dailyApi } from '../api/client'
import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import type {
  ActivityEntry,
  AutomationPolicy,
  CleanupPolicy,
  CleanupPreview,
  CleanupPreviewEntry,
} from '../types'
import { actorLabel, eventLabel, formatBytes } from '../utils/activity'
import { formatShanghai } from '../utils/format'

const form = reactive<CleanupPolicy>({
  cleanup_enabled: false,
  cleanup_dry_run: true,
  cleanup_after_days: 10,
  cleanup_min_seeding_days: 10,
  cleanup_grace_days: 2,
  cleanup_require_library_confirmed: true,
  cleanup_daily_limit: 20,
  cleanup_release_backlog: false,
})
// What the server is acting on, as opposed to what is typed into the form.
const saved = ref<CleanupPolicy | null>(null)
const preview = ref<CleanupPreview | null>(null)
const recent = ref<ActivityEntry[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const previewError = ref<string | null>(null)
const feedback = ref<string | null>(null)
const saving = ref(false)
const changingId = ref<string | null>(null)

function message(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback
}

function applyPolicy(policy: AutomationPolicy): void {
  const values: CleanupPolicy = {
    cleanup_enabled: policy.cleanup_enabled,
    cleanup_dry_run: policy.cleanup_dry_run,
    cleanup_after_days: policy.cleanup_after_days,
    cleanup_min_seeding_days: policy.cleanup_min_seeding_days,
    cleanup_grace_days: policy.cleanup_grace_days,
    cleanup_require_library_confirmed: policy.cleanup_require_library_confirmed,
    cleanup_daily_limit: policy.cleanup_daily_limit,
    // An older server does not send this; absent means off.
    cleanup_release_backlog: policy.cleanup_release_backlog ?? false,
  }
  Object.assign(form, values)
  saved.value = values
}

async function loadPreview(): Promise<void> {
  previewError.value = null
  try {
    preview.value = await dailyApi.cleanupPreview()
  } catch (caught) {
    preview.value = null
    previewError.value = message(caught, '无法读取清理清单')
  }
}

async function loadRecent(): Promise<void> {
  try {
    recent.value = (await activityApi.list({ category: 'cleanup', pageSize: 8 })).items
  } catch {
    // The list above is what matters; the history has its own page.
    recent.value = []
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    applyPolicy(await automationApi.policy())
    await Promise.all([loadPreview(), loadRecent()])
  } catch (caught) {
    error.value = message(caught, '无法读取清理设置')
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  error.value = null
  feedback.value = null
  try {
    applyPolicy(await automationApi.updateCleanupPolicy({ ...form }))
    feedback.value = '清理设置已保存'
    await Promise.all([loadPreview(), loadRecent()])
  } catch (caught) {
    error.value = message(caught, '无法保存清理设置')
  } finally {
    saving.value = false
  }
}

async function setHold(item: CleanupPreviewEntry, held: boolean): Promise<void> {
  if (changingId.value) return
  changingId.value = item.download_id
  previewError.value = null
  try {
    await dailyApi.setCleanupHold(item.download_id, held)
    await Promise.all([loadPreview(), loadRecent()])
  } catch (caught) {
    previewError.value = message(caught, '无法修改清理设置')
  } finally {
    changingId.value = null
  }
}

const items = computed(() => preview.value?.items ?? [])
const bySize = (a: CleanupPreviewEntry, b: CleanupPreviewEntry) => a.size_bytes - b.size_bytes

const marked = computed(() => items.value.filter((item) => item.cleanup_state === 'MARKED'))
// Released and meeting every condition: the next cycle tags these.
const queued = computed(() =>
  items.value.filter((item) => item.cleanup_state === 'NONE' && item.blocked_reason === null),
)
const waiting = computed(() =>
  items.value.filter((item) => item.cleanup_state === 'NONE' && item.blocked_reason !== null),
)
const heldReady = computed(() =>
  items.value
    .filter((item) => item.cleanup_state === 'HELD' && item.blocked_reason === null)
    .sort(bySize),
)
const heldBlocked = computed(() =>
  items.value.filter((item) => item.cleanup_state === 'HELD' && item.blocked_reason !== null),
)

function total(list: CleanupPreviewEntry[]): string {
  return formatBytes(list.reduce((sum, item) => sum + item.size_bytes, 0))
}

const deleting = computed(
  () =>
    saved.value !== null &&
    saved.value.cleanup_enabled &&
    !saved.value.cleanup_dry_run &&
    preview.value?.delete_authorized !== false,
)

const status = computed(() => {
  if (!saved.value?.cleanup_enabled) {
    return { value: '未启用', detail: '不会标记，也不会删除', tone: 'idle' }
  }
  if (deleting.value) {
    return { value: '正在运行', detail: '符合条件的种子会被删除', tone: 'live' }
  }
  if (saved.value.cleanup_dry_run) {
    return { value: '演练中', detail: '只记录，不标记也不删除', tone: 'dry' }
  }
  return { value: '未授权删除', detail: '服务端未开启 ENABLE_QB_DELETE', tone: 'dry' }
})

// A ticked box changes nothing until it is saved; say so while they differ.
const dirty = computed(() => {
  const current = saved.value
  if (current === null) return false
  return (Object.keys(current) as Array<keyof CleanupPolicy>).some(
    (key) => form[key] !== current[key],
  )
})

// Saving this form would start real deletion where there was none.
const armsDeletion = computed(
  () => form.cleanup_enabled && !form.cleanup_dry_run && !deleting.value,
)
const riskyDays = computed(
  () => form.cleanup_after_days < 8 || form.cleanup_min_seeding_days < 8,
)

// A hold the operator placed is theirs; only the backlog is offered in bulk.
function isBacklog(item: CleanupPreviewEntry): boolean {
  return item.hold_reason !== 'MANUAL' && item.hold_reason !== 'TAG_REMOVED'
}
const heldBacklogReady = computed(() => heldReady.value.filter(isBacklog))
// Saving would let the backlog in where it was being kept out.
const armsBacklog = computed(
  () => form.cleanup_release_backlog && saved.value?.cleanup_release_backlog === false,
)

const selectedIds = ref<string[]>([])
const confirmingBulk = ref(false)
const releasing = ref(false)
const selectedItems = computed(() =>
  heldBacklogReady.value.filter((item) => selectedIds.value.includes(item.download_id)),
)
const allSelected = computed(
  () =>
    heldBacklogReady.value.length > 0 &&
    selectedItems.value.length === heldBacklogReady.value.length,
)

function toggle(item: CleanupPreviewEntry): void {
  confirmingBulk.value = false
  selectedIds.value = selectedIds.value.includes(item.download_id)
    ? selectedIds.value.filter((id) => id !== item.download_id)
    : [...selectedIds.value, item.download_id]
}

function toggleAll(): void {
  confirmingBulk.value = false
  selectedIds.value = allSelected.value
    ? []
    : heldBacklogReady.value.map((item) => item.download_id)
}

async function releaseSelected(): Promise<void> {
  const ids = selectedItems.value.map((item) => item.download_id)
  if (!ids.length || releasing.value) return
  releasing.value = true
  previewError.value = null
  feedback.value = null
  try {
    const result = await dailyApi.setCleanupHolds(ids, false)
    feedback.value = result.skipped.length
      ? `已放行 ${result.changed} 项，${result.skipped.length} 项未能放行：${result.skipped[0]?.reason}`
      : `已放行 ${result.changed} 项`
    selectedIds.value = []
    await Promise.all([loadPreview(), loadRecent()])
  } catch (caught) {
    previewError.value = message(caught, '无法批量放行')
  } finally {
    releasing.value = false
    confirmingBulk.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <section class="page cleanup-page">
    <PageHeader
      eyebrow="CLEANUP"
      title="空间清理"
      description="资源入库后，下载目录里那一份就多余了。这里决定哪些种子连同文件一起删除。"
    >
      <button class="button secondary" :disabled="loading" @click="load">刷新</button>
    </PageHeader>

    <PageState :loading="loading" :error="error" />
    <p v-if="feedback" class="configuration-message success">{{ feedback }}</p>

    <template v-if="!loading && saved">
      <div class="summary-grid cleanup-summary" aria-label="清理概览">
        <div class="summary-card" :class="`cleanup-status-${status.tone}`">
          <span>
            <small>当前状态</small>
            <strong>{{ status.value }}</strong>
            <em>{{ status.detail }}</em>
          </span>
        </div>
        <div class="summary-card">
          <span>
            <small>待删除</small>
            <strong>{{ marked.length + queued.length }} 项</strong>
            <em>{{ total([...marked, ...queued]) }}</em>
          </span>
        </div>
        <div class="summary-card">
          <span>
            <small>保留中，可放行</small>
            <strong>{{ heldReady.length }} 项</strong>
            <em>{{ total(heldReady) }}</em>
          </span>
        </div>
        <div class="summary-card">
          <span>
            <small>删除前观察</small>
            <strong>{{ saved.cleanup_grace_days }} 天</strong>
            <em>打上标签后等这么久才删</em>
          </span>
        </div>
      </div>

      <p v-if="previewError" class="inline-warning">{{ previewError }}</p>

      <form class="panel cleanup-panel" @submit.prevent="save">
        <div class="section-heading">
          <div>
            <h2>设置</h2>
            <p class="muted">
              删除不可逆。真正删除需要同时满足：启用、关闭演练模式、服务端开启
              <code>ENABLE_QB_DELETE</code>{{ preview && !preview.delete_authorized ? '（当前未开启）' : '' }}。
            </p>
          </div>
        </div>
        <div class="configuration-field-grid cleanup-switches">
          <label class="configuration-checkbox">
            <input v-model="form.cleanup_enabled" name="cleanup_enabled" type="checkbox" />
            启用空间清理
          </label>
          <label class="configuration-checkbox">
            <input v-model="form.cleanup_dry_run" name="cleanup_dry_run" type="checkbox" />
            演练模式（只记录，不删除）
          </label>
          <label class="configuration-checkbox">
            <input
              v-model="form.cleanup_require_library_confirmed"
              name="cleanup_require_library_confirmed"
              type="checkbox"
            />
            必须已确认入库
          </label>
          <label class="configuration-checkbox">
            <input v-model="form.cleanup_release_backlog" name="cleanup_release_backlog" type="checkbox" />
            自动放行存量（满足条件即清理）
          </label>
        </div>
        <div class="configuration-field-grid cleanup-numbers">
          <label>
            保留天数（下载完成后）
            <input v-model.number="form.cleanup_after_days" name="cleanup_after_days" type="number" min="1" max="365" />
          </label>
          <label>
            最短做种天数
            <input
              v-model.number="form.cleanup_min_seeding_days"
              name="cleanup_min_seeding_days"
              type="number"
              min="1"
              max="365"
            />
          </label>
          <label>
            标记后观察天数
            <input v-model.number="form.cleanup_grace_days" name="cleanup_grace_days" type="number" min="0" max="30" />
          </label>
          <label>
            每日最多删除
            <input v-model.number="form.cleanup_daily_limit" name="cleanup_daily_limit" type="number" min="1" max="500" />
          </label>
        </div>
        <p v-if="riskyDays" class="inline-warning">
          AvistaZ 要求做种满 7 天，而客户端统计的做种时长通常比站点认可的更长。
          低于 8 天有被记 H&amp;R 的风险，建议保持 10 天。
        </p>
        <p v-if="armsDeletion" class="inline-warning">
          保存后将开始真正删除：「待删除」里的 {{ marked.length + queued.length }} 项会在下一轮被标记，观察期后删除。
        </p>
        <p v-if="armsBacklog" class="inline-warning">
          保存后，「保留中」里 {{ heldBacklogReady.length }} 项满足条件的存量（{{ total(heldBacklogReady) }}）会在下一轮自动放行并打上标签，
          观察 {{ form.cleanup_grace_days }} 天后删除，每天最多删 {{ form.cleanup_daily_limit }} 个；以后其余存量满足条件时也会自动处理。
          你手动保留的和在 qBittorrent 里摘过标签的不受影响。
        </p>
        <div class="filter-actions cleanup-save">
          <span v-if="dirty" class="cleanup-unsaved" role="status">
            有未保存的修改，点「保存设置」后才生效
          </span>
          <span v-else class="muted">当前显示的就是正在生效的设置</span>
          <button class="button primary" type="submit" :disabled="saving || !dirty">
            {{ saving ? '保存中…' : '保存设置' }}
          </button>
        </div>
      </form>

      <article class="panel cleanup-panel">
        <div class="section-heading">
          <div>
            <h2>待删除</h2>
            <p class="muted">
              已放行的种子。先打上 <code>unin-cleanup</code> 标签，观察期过后删除种子和文件。
              在 qBittorrent 里摘掉标签可以保住它。
            </p>
          </div>
        </div>
        <p v-if="!marked.length && !queued.length && !waiting.length" class="muted cleanup-empty">
          没有已放行的种子。从下面「保留中」里放行，它们才会进入这里。
        </p>
        <ul v-if="marked.length || queued.length" class="cleanup-list">
          <li v-for="item in marked" :key="item.download_id">
            <span>
              <strong>{{ item.media_title }}</strong>
              <span class="muted">{{ item.name }} · {{ formatBytes(item.size_bytes) }}</span>
            </span>
            <span class="status-pill activity-cleanup">
              已标记<template v-if="item.deletes_at"> · {{ formatShanghai(item.deletes_at) }} 后删除</template>
            </span>
          </li>
          <li v-for="item in queued" :key="item.download_id">
            <span>
              <strong>{{ item.media_title }}</strong>
              <span class="muted">
                {{ item.name }} · {{ formatBytes(item.size_bytes) }} · 已做种 {{ item.seeding_days }} 天
              </span>
            </span>
            <span class="cleanup-row-actions">
              <span class="status-pill">{{ deleting ? '下一轮标记' : '启用后标记' }}</span>
              <button
                type="button"
                class="button secondary small"
                :disabled="changingId !== null"
                @click="setHold(item, true)"
              >
                保留
              </button>
            </span>
          </li>
        </ul>
        <ul v-if="waiting.length" class="cleanup-list">
          <li v-for="item in waiting" :key="item.download_id">
            <span>
              <strong>{{ item.media_title }}</strong>
              <span class="muted">{{ item.name }} · 暂不满足：{{ item.blocked_reason }}</span>
            </span>
            <button
              type="button"
              class="button secondary small"
              :disabled="changingId !== null"
              @click="setHold(item, true)"
            >
              保留
            </button>
          </li>
        </ul>
      </article>

      <article class="panel cleanup-panel">
        <div class="section-heading">
          <div>
            <h2>保留中</h2>
            <p class="muted">
              不会被自动清理。下面 {{ heldReady.length }} 项已满足全部条件，放行后就会被删除；
              放行前请先确认媒体库里那一份完好。
            </p>
          </div>
        </div>
        <p v-if="saved.cleanup_release_backlog && heldBacklogReady.length" class="inline-warning">
          「自动放行存量」已开启：下面没有标注「手动保留」的种子会在下一轮自动进入待删除，不需要逐个放行。
        </p>
        <p v-if="!heldReady.length" class="muted cleanup-empty">没有可以放行的种子。</p>
        <div v-if="heldReady.length" class="cleanup-bulk">
          <label class="cleanup-select-all">
            <input
              type="checkbox"
              name="select_all"
              :checked="allSelected"
              :disabled="!heldBacklogReady.length || releasing"
              @change="toggleAll"
            />
            全选（{{ heldBacklogReady.length }} 项）
          </label>
          <span class="muted">已选 {{ selectedItems.length }} 项 · {{ total(selectedItems) }}</span>
          <span class="cleanup-bulk-actions">
            <template v-if="confirmingBulk">
              <button type="button" class="button secondary small" :disabled="releasing" @click="confirmingBulk = false">
                取消
              </button>
              <button type="button" class="button primary small" :disabled="releasing" @click="releaseSelected">
                {{ releasing ? '放行中…' : `确认放行 ${selectedItems.length} 项（${total(selectedItems)}）` }}
              </button>
            </template>
            <button
              v-else
              type="button"
              class="button primary small"
              :disabled="!selectedItems.length || changingId !== null"
              @click="confirmingBulk = true"
            >
              放行所选
            </button>
          </span>
        </div>
        <ul v-if="heldReady.length" class="cleanup-list">
          <li v-for="item in heldReady" :key="item.download_id">
            <input
              v-if="isBacklog(item)"
              type="checkbox"
              class="cleanup-row-check"
              :aria-label="`选择 ${item.media_title}`"
              :checked="selectedIds.includes(item.download_id)"
              :disabled="releasing"
              @change="toggle(item)"
            />
            <span v-else class="cleanup-row-check" aria-hidden="true"></span>
            <span>
              <strong>
                {{ item.media_title }}
                <span v-if="!isBacklog(item)" class="status-pill">
                  {{ item.hold_reason === 'TAG_REMOVED' ? '摘标签保留' : '手动保留' }}
                </span>
              </strong>
              <span class="muted">
                {{ item.name }} · {{ formatBytes(item.size_bytes) }} · 已做种 {{ item.seeding_days }} 天（需
                {{ item.required_seeding_days }} 天）
              </span>
            </span>
            <button
              type="button"
              class="button secondary small"
              :disabled="changingId !== null || releasing"
              @click="setHold(item, false)"
            >
              {{ changingId === item.download_id ? '放行中…' : '放行' }}
            </button>
          </li>
        </ul>
        <details v-if="heldBlocked.length" class="advanced-settings">
          <summary>另有 {{ heldBlocked.length }} 项即使放行也不会删除</summary>
          <ul class="cleanup-list">
            <li v-for="item in heldBlocked" :key="item.download_id">
              <span>
                <strong>{{ item.media_title }}</strong>
                <span class="muted">{{ item.name }} · {{ item.blocked_reason }}</span>
              </span>
            </li>
          </ul>
        </details>
      </article>

      <article class="panel cleanup-panel">
        <div class="section-heading">
          <div><h2>最近的清理记录</h2></div>
          <RouterLink class="button secondary small" to="/activity">全部记录</RouterLink>
        </div>
        <p v-if="!recent.length" class="muted cleanup-empty">还没有清理记录。</p>
        <ul v-else class="cleanup-list">
          <li v-for="entry in recent" :key="entry.id">
            <span>
              <strong>{{ eventLabel(entry.event) }}</strong>
              <span class="muted">{{ entry.message }}</span>
            </span>
            <span class="muted cleanup-recent-meta">
              {{ actorLabel(entry.actor) }} · {{ formatShanghai(entry.created_at) }}
            </span>
          </li>
        </ul>
      </article>
    </template>
  </section>
</template>
