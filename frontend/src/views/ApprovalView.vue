<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useApprovalStore } from '../stores/approvals'
import { useAuthStore } from '../stores/auth'
import { useExecutionStore } from '../stores/executions'
import type { ApprovalCandidateSnapshot, ApprovalStatus, DownloadLaunchMode } from '../types'
import { formatShanghai } from '../utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const store = useApprovalStore()
const executionStore = useExecutionStore()
const ttl = ref(60)
const reason = ref('')
const acknowledgesHnr = ref(false)
const acknowledgesSeeding = ref(false)
const acknowledgesPlanOnly = ref(false)
const launchMode = ref<DownloadLaunchMode>('ADD_PAUSED')
const submittingExecution = ref(false)
const restartableApprovalStatuses = new Set<ApprovalStatus>(['REJECTED', 'EXPIRED', 'REVOKED'])

const snapshot = computed<ApprovalCandidateSnapshot | null>(() => {
  if (store.approval) return store.approval.candidate
  if (!store.candidate || !store.media) return null
  const item = store.candidate.candidate
  return {
    media_item_id: store.media.id,
    media_title: store.media.title,
    media_type: store.media.media_type,
    tmdb_id: store.media.tmdb_id,
    year: store.media.year,
    torrent_candidate_id: store.candidate.id,
    site_id: item.site_id,
    torrent_id: item.torrent_id,
    torrent_ref: item.details_ref,
    release_title: item.release_title,
    size_bytes: item.size_bytes,
    info_hash: item.info_hash,
    season: item.season,
    episodes: item.episodes,
    resolution: item.resolution,
    source: item.source,
    subtitles: item.subtitles,
    seeders: item.seeders,
    promotion: { download_factor: item.download_factor, upload_factor: item.upload_factor },
    hit_and_run: item.hit_and_run,
    match_score: store.candidate.match_score,
    match_reasons: store.candidate.match_reasons,
    warnings: store.candidate.warnings,
    requested_at: '',
    expires_at: '',
  }
})

const isAvistaZ = computed(
  () => snapshot.value?.site_id.trim().toLowerCase() === 'avistaz',
)
const requiresHnrAcknowledgement = computed(() => !isAvistaZ.value)
const visibleWarnings = computed(() => {
  const warnings = snapshot.value?.warnings ?? []
  if (!isAvistaZ.value) return warnings
  return warnings.filter((warning) => warning.trim().toUpperCase() !== 'HNR_UNKNOWN')
})
const acknowledgementsComplete = computed(
  () =>
    (!requiresHnrAcknowledgement.value || acknowledgesHnr.value) &&
    acknowledgesSeeding.value &&
    acknowledgesPlanOnly.value,
)
const canOperate = computed(() => auth.hasRole('operator'))
const canAdmin = computed(() => auth.hasRole('admin'))
const executionBusy = computed(() => executionStore.working || submittingExecution.value)
const selectedExecution = computed(() => {
  if (!store.approval || executionStore.selected?.approval_id !== store.approval.id) return null
  return executionStore.selected
})
const returnTarget = computed(() => {
  const mediaId = route.params.mediaId
  return mediaId
    ? {
        href: `/media/${encodeURIComponent(String(mediaId))}/torrents`,
        label: '返回 PT 候选',
      }
    : { href: '/approvals', label: '返回审批列表' }
})
const routeTarget = computed(() => {
  if (route.params.id) return `approval:${String(route.params.id)}`
  return [
    'candidate',
    String(route.params.mediaId),
    String(route.params.searchId),
    String(route.params.candidateId),
  ].join(':')
})
let routeGeneration = 0

function resetLocalConfirmations(): void {
  reason.value = ''
  acknowledgesHnr.value = false
  acknowledgesSeeding.value = false
  acknowledgesPlanOnly.value = false
  launchMode.value = 'ADD_PAUSED'
  submittingExecution.value = false
}

async function loadRouteTarget(): Promise<void> {
  const current = ++routeGeneration
  resetLocalConfirmations()
  executionStore.resetControl()
  const approvalId = route.params.id
  if (approvalId) {
    const expectedApprovalId = String(approvalId)
    await store.loadApproval(expectedApprovalId)
    if (current !== routeGeneration) return
    if (store.approval?.id === expectedApprovalId) {
      await executionStore.loadForApproval(expectedApprovalId)
    }
    return
  }
  const mediaId = String(route.params.mediaId)
  const searchId = String(route.params.searchId)
  const candidateId = String(route.params.candidateId)
  await store.loadCandidate(
    mediaId,
    searchId,
    candidateId,
  )
  if (current !== routeGeneration) return
  if (store.approval?.torrent_candidate_id === candidateId) {
    await executionStore.loadForApproval(store.approval.id)
  }
}

watch(routeTarget, () => void loadRouteTarget(), { immediate: true })
onBeforeUnmount(() => {
  routeGeneration += 1
  executionStore.resetControl()
})

function formatBytes(value: number | null): string {
  if (value === null) return '未知'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`
}

async function createApproval(): Promise<void> {
  if (!snapshot.value || !canOperate.value) return
  const current = routeGeneration
  const candidateId = snapshot.value.torrent_candidate_id
  const routeApprovalId = route.params.id ? String(route.params.id) : null
  const created = await store.create(candidateId, ttl.value)
  if (!created || current !== routeGeneration) return
  if (routeApprovalId && String(route.params.id) !== routeApprovalId) return
  await router.replace(`/approvals/${encodeURIComponent(created.id)}`)
}

function selectLaunchMode(mode: DownloadLaunchMode): void {
  if (executionBusy.value) return
  launchMode.value = mode
}

async function submitExecution(): Promise<void> {
  if (!store.approval || !canAdmin.value || executionBusy.value) return
  const approvalId = store.approval.id
  submittingExecution.value = true
  try {
    if (!await executionStore.createIntent(approvalId, launchMode.value)) return
    await executionStore.executeIntent(approvalId)
  } finally {
    submittingExecution.value = false
  }
}

async function approvePlan(): Promise<void> {
  if (!store.approval) return
  const approvalId = store.approval.id
  const result = await store.approve(
    approvalId,
    requiresHnrAcknowledgement.value ? acknowledgesHnr.value : false,
  )
  if (result?.id === approvalId && result.status === 'APPROVED') {
    await executionStore.loadForApproval(approvalId)
  }
}
</script>

<template>
  <section class="page wide-page">
    <PageHeader
      eyebrow="MANUAL APPROVAL"
      title="种子人工审批"
      description="审批绑定不可变候选快照；候选变化不会继承旧审批。"
    >
      <div class="header-actions">
        <a class="button secondary" :href="returnTarget.href">{{ returnTarget.label }}</a>
      </div>
    </PageHeader>
    <div class="phase-banner"><span>受控执行</span>审批只生成不可执行计划；后续由管理员一次确认下载执行，或显式启用并满足自动执行策略，且服务端执行总闸开启时，才可能写入 qBittorrent。默认添加后暂停。</div>
    <PageState :loading="store.loading" :error="store.error" />
    <div v-if="store.notice" class="notice-state">{{ store.notice }}</div>
    <div v-if="store.planError" class="notice-state warning-state">{{ store.planError }}</div>
    <div v-if="executionStore.error" class="notice-state warning-state">{{ executionStore.error }}</div>
    <div v-if="executionStore.notice" class="notice-state">{{ executionStore.notice }}</div>

    <template v-if="snapshot && !store.loading">
      <section class="source-strip approval-source">
        <div><span>影视</span><strong>{{ snapshot.media_title }}</strong></div>
        <div><span>类型</span><strong>{{ snapshot.media_type === 'movie' ? '电影' : '电视剧' }}</strong></div>
        <div><span>TMDB</span><strong class="mono">{{ snapshot.tmdb_id ?? '未知' }}</strong></div>
        <div><span>年份</span><strong>{{ snapshot.year ?? '未知' }}</strong></div>
        <div><span>审批状态</span><StatusPill :status="store.approval?.status ?? 'NOT_REQUESTED'" /></div>
      </section>

      <section class="approval-layout">
        <div class="approval-main">
          <div class="approval-section">
            <span class="eyebrow">FIXED CANDIDATE SNAPSHOT</span>
            <h2>{{ snapshot.release_title }}</h2>
            <dl class="approval-facts">
              <div><dt>站点 / 种子 ID</dt><dd>{{ snapshot.site_id }} / {{ snapshot.torrent_id }}</dd></div>
              <div><dt>info_hash</dt><dd class="mono">{{ snapshot.info_hash ?? '未知' }}</dd></div>
              <div><dt>季 / 集</dt><dd>{{ snapshot.season ?? '—' }} / {{ snapshot.episodes?.join(', ') || '—' }}</dd></div>
              <div><dt>规格</dt><dd>{{ snapshot.resolution || '—' }} · {{ snapshot.source || '—' }}</dd></div>
              <div><dt>字幕</dt><dd>{{ snapshot.subtitles?.join(' / ') || '—' }}</dd></div>
              <div><dt>大小 / 做种</dt><dd>{{ formatBytes(snapshot.size_bytes) }} / {{ snapshot.seeders ?? '未知' }}</dd></div>
              <div><dt>促销</dt><dd>下载 {{ snapshot.promotion.download_factor ?? '未知' }} · 上传 {{ snapshot.promotion.upload_factor ?? '未知' }}</dd></div>
              <div><dt>H&amp;R</dt><dd :class="!isAvistaZ && snapshot.hit_and_run === null ? 'risk-text' : ''">{{ isAvistaZ ? '站点默认 7 天' : snapshot.hit_and_run === null ? '未知，禁止视为通过' : snapshot.hit_and_run ? '适用' : '不适用' }}</dd></div>
            </dl>
            <div class="score-summary"><strong>{{ Math.round(snapshot.match_score * 100) }}</strong><div class="reason-list"><span v-for="item in snapshot.match_reasons" :key="item">{{ item }}</span></div></div>
            <div v-if="visibleWarnings.length" class="warning-list"><span v-for="item in visibleWarnings" :key="item">{{ item }}</span></div>
          </div>

          <div v-if="store.approval?.preflight_result" class="approval-section">
            <div class="section-heading"><div><span class="eyebrow">READ-ONLY PREFLIGHT</span><h2>下载前预检</h2></div><StatusPill :status="store.approval.preflight_result.overall_status" /></div>
            <div class="preflight-list">
              <article v-for="check in store.approval.preflight_result.checks" :key="check.code" :class="`check-${check.status.toLowerCase()}`">
                <StatusPill :status="check.status" /><div><strong>{{ check.code }}</strong><p>{{ check.message }}</p></div>
              </article>
            </div>
          </div>

          <div v-if="store.plan" class="approval-section download-plan">
            <span class="eyebrow">APPROVED PLAN</span><h2>下载计划</h2>
            <dl class="approval-facts">
              <div><dt>内部种子引用</dt><dd class="mono">{{ store.plan.torrent_ref }}</dd></div>
              <div><dt>预期 info_hash</dt><dd class="mono">{{ store.plan.expected_info_hash ?? '未知' }}</dd></div>
              <div><dt>保存位置引用</dt><dd>{{ store.plan.save_path_ref }}</dd></div>
              <div><dt>分类 / 标签</dt><dd>{{ store.plan.category }} / {{ store.plan.tags.join(', ') || '—' }}</dd></div>
              <div><dt>预计大小</dt><dd>{{ formatBytes(store.plan.estimated_size_bytes) }}</dd></div>
              <div><dt>模式</dt><dd>APPROVED_IMMUTABLE_PLAN</dd></div>
            </dl>
            <div v-if="!selectedExecution && executionStore.approvalLookupStatus === 'not_found'" class="phase-banner inline-banner"><span>尚未提交</span>选择启动模式后，只需确认一次即可提交下载执行。</div>
          </div>

          <div v-if="(store.approval?.status === 'APPROVED' && store.plan) || selectedExecution" class="approval-section execution-control-section">
            <span class="eyebrow">DOWNLOAD EXECUTION</span><h2>下载执行确认</h2>
            <template v-if="!selectedExecution && executionStore.approvalLookupStatus === 'not_found'">
              <div class="execution-step single-execution-step">
                <div class="segmented-control" role="group" aria-label="下载启动模式">
                  <button type="button" :class="{ active: launchMode === 'ADD_PAUSED' }" :aria-pressed="launchMode === 'ADD_PAUSED'" :disabled="executionBusy" @click="selectLaunchMode('ADD_PAUSED')">
                    <strong>添加后暂停</strong><small>默认，确认任务后再手动开始</small>
                  </button>
                  <button type="button" :class="{ active: launchMode === 'START_IMMEDIATELY' }" :aria-pressed="launchMode === 'START_IMMEDIATELY'" :disabled="executionBusy" @click="selectLaunchMode('START_IMMEDIATELY')">
                    <strong>立即开始</strong><small>添加成功后立即产生下载流量</small>
                  </button>
                </div>
                <div class="execution-checks execution-submit">
                  <button
                    class="button primary"
                    type="button"
                    :disabled="executionBusy || !canAdmin"
                    @click="submitExecution"
                  >
                    {{ executionBusy ? '正在提交…' : launchMode === 'START_IMMEDIATELY' ? '确认并立即下载' : '确认添加到 qB' }}
                  </button>
                </div>
              </div>
              <p v-if="!canAdmin" class="permission-hint">当前角色仅可查看；执行写操作需要管理员权限。</p>
            </template>

            <div v-else-if="selectedExecution" class="execution-created">
              <div><span class="eyebrow">EXECUTION CREATED</span><StatusPill :status="selectedExecution.status" /></div>
              <h3>已存在下载执行记录，不会重复创建</h3>
              <dl class="approval-facts">
                <div><dt>启动模式</dt><dd>{{ selectedExecution.launch_mode === 'ADD_PAUSED' ? '添加后暂停' : '立即开始' }}</dd></div>
                <div><dt>请求时间</dt><dd>{{ formatShanghai(selectedExecution.requested_at) }}</dd></div>
                <div><dt>尝试次数</dt><dd>{{ selectedExecution.attempts }} / {{ selectedExecution.max_attempts }}</dd></div>
                <div><dt>执行错误</dt><dd>{{ selectedExecution.error_code ?? '无' }}</dd></div>
              </dl>
              <p v-if="selectedExecution.error_message" class="risk-text">{{ selectedExecution.error_message }}</p>
              <p v-else-if="selectedExecution.requires_reconciliation" class="risk-text">该执行需要人工对账，请进入执行详情处理。</p>
              <p v-else>创建记录不等同于下载成功。请进入执行详情查看校验、提交和最终状态。</p>
              <div class="header-actions">
                <a class="button secondary" :href="`/executions/${selectedExecution.id}`">查看执行详情</a>
                <a
                  v-if="executionStore.downloadJob"
                  class="button primary"
                  :href="`/download-jobs/${executionStore.downloadJob.id}`"
                >查看下载任务总结</a>
                <button
                  v-else-if="['not_created', 'timed_out', 'error'].includes(executionStore.downloadJobLookupStatus)"
                  class="button secondary"
                  type="button"
                  @click="executionStore.pollDownloadJobForExecution(selectedExecution.id, selectedExecution.status)"
                >
                  重新查询下载任务
                </button>
              </div>
              <small v-if="executionStore.downloadJobLookupStatus === 'polling'">正在等待执行器创建下载任务…</small>
              <small v-else-if="executionStore.downloadJobError" class="risk-text">{{ executionStore.downloadJobError }}</small>
            </div>
            <div v-else class="notice-state warning-state execution-lookup-guard" role="status">
              {{ executionStore.approvalLookupStatus === 'loading'
                ? '正在确认该审批是否已有下载执行记录，确认完成前不会开放重复执行入口。'
                : executionStore.approvalLookupStatus === 'error'
                  ? '无法确认该审批是否已有下载执行记录，手工执行入口已安全禁用；请刷新后重试。'
                  : '尚未完成关联执行检查，手工执行入口暂不开放。' }}
            </div>
          </div>
        </div>

        <aside class="approval-controls">
          <div class="approval-actor">
            <span>当前登录账号</span>
            <strong>{{ auth.principal?.username }} · {{ auth.roleLabel }}</strong>
            <small>审计操作者由服务端会话确定；服务端仍会独立校验权限。</small>
          </div>
          <template v-if="!store.approval || restartableApprovalStatuses.has(store.approval.status)">
            <div v-if="store.approval" class="notice-state warning-state">
              上一次审批状态为 {{ store.approval.status }}，可基于当前固定候选重新发起审批。
            </div>
            <label>审批有效期（分钟）<input v-model.number="ttl" type="number" min="5" max="10080" /></label>
            <button class="button primary" :disabled="store.working || !canOperate" @click="createApproval">
              {{ store.approval ? '重新发起审批' : '创建固定快照审批' }}
            </button>
          </template>
          <template v-else>
            <div class="approval-expiry"><span>申请时间</span><strong>{{ formatShanghai(store.approval.requested_at) }}</strong><span>到期时间</span><strong>{{ formatShanghai(store.approval.expires_at) }}</strong></div>
            <div v-if="store.approval.status === 'PENDING'" class="acknowledgements">
              <label v-if="requiresHnrAcknowledgement"><input v-model="acknowledgesHnr" type="checkbox" :disabled="!canAdmin || store.working" />已了解该站 H&amp;R 规则</label>
              <label><input v-model="acknowledgesSeeding" type="checkbox" :disabled="!canAdmin || store.working" />下载完成后需要继续做种</label>
              <label><input v-model="acknowledgesPlanOnly" type="checkbox" :disabled="!canAdmin || store.working" />我确认审批本身只生成不可执行计划；若已显式启用且满足自动执行策略，独立 ADD_PAUSED 执行可能随即排队</label>
            </div>
            <button
              v-if="store.approval.status === 'PENDING'"
              class="button primary"
              :disabled="store.working || !canAdmin || !acknowledgementsComplete"
              @click="approvePlan"
            >
              {{ store.working ? '正在预检并批准…' : '预检并批准计划' }}
            </button>
            <label v-if="store.approval.status === 'PENDING' || store.approval.status === 'APPROVED'">原因（可选）<textarea v-model="reason" maxlength="1000" :disabled="store.approval.status === 'PENDING' ? !canOperate : !canAdmin" /></label>
            <button v-if="store.approval.status === 'PENDING'" class="button danger-button" :disabled="store.working || !canOperate" @click="store.reject(store.approval.id, reason)">拒绝</button>
            <button v-if="store.approval.status === 'APPROVED'" class="button danger-button" :disabled="store.working || !canAdmin" @click="store.revoke(store.approval.id, reason)">撤销审批</button>
          </template>
        </aside>
      </section>

      <section v-if="store.approval?.events.length" class="approval-audit">
        <span class="eyebrow">AUDIT TRAIL</span><h2>审批事件</h2>
        <div class="timeline"><article v-for="event in store.approval.events" :key="event.id"><span class="timeline-dot"></span><div><strong>{{ event.event_type }} · {{ event.actor }}</strong><small>{{ formatShanghai(event.created_at) }} · {{ event.from_status ?? '—' }} → {{ event.to_status }}</small><p v-if="event.reason">{{ event.reason }}</p></div></article></div>
      </section>
    </template>
  </section>
</template>
