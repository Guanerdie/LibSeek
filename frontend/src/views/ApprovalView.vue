<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useApprovalStore } from '../stores/approvals'
import { useAuthStore } from '../stores/auth'
import { useExecutionStore } from '../stores/executions'
import type { ApprovalCandidateSnapshot, DownloadLaunchMode } from '../types'
import { formatShanghai } from '../utils/format'

const route = useRoute()
const auth = useAuthStore()
const store = useApprovalStore()
const executionStore = useExecutionStore()
const ttl = ref(60)
const reason = ref('')
const acknowledgesHnr = ref(false)
const acknowledgesSeeding = ref(false)
const acknowledgesPlanOnly = ref(false)
const launchMode = ref<DownloadLaunchMode>('ADD_PAUSED')
const executionPlanConfirmed = ref(false)
const immediateStartConfirmed = ref(false)
const finalExecutionConfirmed = ref(false)

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

const acknowledgementsComplete = computed(
  () => acknowledgesHnr.value && acknowledgesSeeding.value && acknowledgesPlanOnly.value,
)
const preflightPassable = computed(() => {
  const status = store.approval?.preflight_result?.overall_status
  return status === 'PASS' || status === 'WARNING'
})
const canOperate = computed(() => auth.hasRole('operator'))
const canAdmin = computed(() => auth.hasRole('admin'))

onMounted(async () => {
  executionStore.resetControl()
  const approvalId = route.params.id
  if (approvalId) {
    await store.loadApproval(String(approvalId))
    if (store.approval?.status === 'EXECUTING' || store.approval?.status === 'CONSUMED') {
      await executionStore.loadForApproval(store.approval.id)
    }
    return
  }
  void store.loadCandidate(
    String(route.params.mediaId),
    String(route.params.searchId),
    String(route.params.candidateId),
  )
})
onBeforeUnmount(() => executionStore.discardIntent())

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

function createApproval(): void {
  if (!snapshot.value || !canOperate.value) return
  void store.create(snapshot.value.torrent_candidate_id, ttl.value)
}

function selectLaunchMode(mode: DownloadLaunchMode): void {
  if (executionStore.intent || executionStore.working) return
  launchMode.value = mode
  executionPlanConfirmed.value = false
  immediateStartConfirmed.value = false
  finalExecutionConfirmed.value = false
}

async function createExecutionIntent(): Promise<void> {
  if (
    !store.approval ||
    !canAdmin.value ||
    !executionPlanConfirmed.value ||
    (launchMode.value === 'START_IMMEDIATELY' && !immediateStartConfirmed.value)
  ) return
  if (await executionStore.createIntent(store.approval.id, launchMode.value)) {
    finalExecutionConfirmed.value = false
  }
}

async function executeApprovedPlan(): Promise<void> {
  if (!store.approval || !canAdmin.value || !finalExecutionConfirmed.value) return
  await executionStore.executeIntent(store.approval.id)
  executionPlanConfirmed.value = false
  immediateStartConfirmed.value = false
  finalExecutionConfirmed.value = false
}
</script>

<template>
  <section class="page wide-page">
    <PageHeader
      eyebrow="MANUAL APPROVAL"
      title="种子人工审批"
      description="审批绑定不可变候选快照；候选变化不会继承旧审批。"
    >
      <div class="header-actions"><a class="button secondary" href="/approvals">返回审批列表</a></div>
    </PageHeader>
    <div class="phase-banner"><span>受控执行</span>仅管理员可在已批准计划上完成两步确认；默认以暂停状态添加到 qBittorrent。</div>
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
              <div><dt>H&R</dt><dd :class="snapshot.hit_and_run === null ? 'risk-text' : ''">{{ snapshot.hit_and_run === null ? '未知，禁止视为通过' : snapshot.hit_and_run ? '适用' : '不适用' }}</dd></div>
            </dl>
            <div class="score-summary"><strong>{{ Math.round(snapshot.match_score * 100) }}</strong><div class="reason-list"><span v-for="item in snapshot.match_reasons" :key="item">{{ item }}</span></div></div>
            <div v-if="snapshot.warnings.length" class="warning-list"><span v-for="item in snapshot.warnings" :key="item">{{ item }}</span></div>
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
            <div v-if="!executionStore.selected" class="phase-banner inline-banner"><span>尚未提交</span>完成下方两步确认前，不会向 qBittorrent 写入任何内容。</div>
          </div>

          <div v-if="(store.approval?.status === 'APPROVED' && store.plan) || executionStore.selected?.approval_id === store.approval?.id" class="approval-section execution-control-section">
            <span class="eyebrow">TWO-STEP EXECUTION</span><h2>下载执行确认</h2>
            <template v-if="!executionStore.selected">
              <div class="execution-step">
                <div class="execution-step-heading"><strong>1</strong><div><h3>创建短期执行意图</h3><p>核对启动模式与批准计划。此步骤尚不会向下载器添加种子。</p></div></div>
                <div class="segmented-control" role="group" aria-label="下载启动模式">
                  <button type="button" :class="{ active: launchMode === 'ADD_PAUSED' }" :aria-pressed="launchMode === 'ADD_PAUSED'" :disabled="Boolean(executionStore.intent) || executionStore.working" @click="selectLaunchMode('ADD_PAUSED')">
                    <strong>添加后暂停</strong><small>默认，确认任务后再手动开始</small>
                  </button>
                  <button type="button" :class="{ active: launchMode === 'START_IMMEDIATELY' }" :aria-pressed="launchMode === 'START_IMMEDIATELY'" :disabled="Boolean(executionStore.intent) || executionStore.working" @click="selectLaunchMode('START_IMMEDIATELY')">
                    <strong>立即开始</strong><small>添加成功后立即产生下载流量</small>
                  </button>
                </div>
                <div v-if="!executionStore.intent" class="execution-checks">
                  <label><input v-model="executionPlanConfirmed" type="checkbox" :disabled="!canAdmin || executionStore.working" />我已核对批准快照、下载计划、保存位置与做种责任</label>
                  <label v-if="launchMode === 'START_IMMEDIATELY'" class="immediate-warning"><input v-model="immediateStartConfirmed" type="checkbox" :disabled="!canAdmin || executionStore.working" />我明确确认选择“立即开始”，提交后会立即产生下载流量</label>
                  <button
                    class="button secondary"
                    :disabled="executionStore.working || !canAdmin || !executionPlanConfirmed || (launchMode === 'START_IMMEDIATELY' && !immediateStartConfirmed)"
                    @click="createExecutionIntent"
                  >
                    {{ executionStore.working ? '创建中…' : '第一步：创建执行意图' }}
                  </button>
                </div>
                <dl v-else class="intent-summary">
                  <div><dt>Intent ID</dt><dd class="mono">{{ executionStore.intent.id }}</dd></div>
                  <div><dt>启动模式</dt><dd>{{ executionStore.intent.launch_mode === 'ADD_PAUSED' ? '添加后暂停' : '立即开始' }}</dd></div>
                  <div><dt>失效时间</dt><dd>{{ formatShanghai(executionStore.intent.expires_at) }}</dd></div>
                  <div><dt>计划哈希</dt><dd class="mono">{{ executionStore.intent.plan_hash }}</dd></div>
                </dl>
              </div>

              <div v-if="executionStore.intent" class="execution-step final-step">
                <div class="execution-step-heading"><strong>2</strong><div><h3>最终提交</h3><p>提交会创建一次性执行记录。安全随机凭据不会显示或保存，失败后也不会复用。</p></div></div>
                <div class="execution-checks">
                  <label><input v-model="finalExecutionConfirmed" type="checkbox" :disabled="!canAdmin || executionStore.working" />我确认现在向 qBittorrent 执行上述已批准计划</label>
                  <button class="button primary" :disabled="executionStore.working || !canAdmin || !finalExecutionConfirmed" @click="executeApprovedPlan">
                    {{ executionStore.working ? '提交中…' : '第二步：提交下载执行' }}
                  </button>
                </div>
              </div>
              <p v-if="!canAdmin" class="permission-hint">当前角色仅可查看；执行写操作需要管理员权限。</p>
            </template>

            <div v-else class="execution-created">
              <div><span class="eyebrow">EXECUTION CREATED</span><StatusPill :status="executionStore.selected.status" /></div>
              <h3>执行记录已创建，后台结果仍需继续观察</h3>
              <p>创建记录不等同于下载成功。请进入执行详情查看校验、提交、错误和对账状态。</p>
              <a class="button secondary" :href="`/executions/${executionStore.selected.id}`">查看执行详情</a>
            </div>
          </div>
        </div>

        <aside class="approval-controls">
          <div class="approval-actor">
            <span>当前登录账号</span>
            <strong>{{ auth.principal?.username }} · {{ auth.roleLabel }}</strong>
            <small>审计操作者由服务端会话确定；服务端仍会独立校验权限。</small>
          </div>
          <template v-if="!store.approval">
            <label>审批有效期（分钟）<input v-model.number="ttl" type="number" min="5" max="10080" /></label>
            <button class="button primary" :disabled="store.working || !canOperate" @click="createApproval">创建固定快照审批</button>
          </template>
          <template v-else>
            <div class="approval-expiry"><span>申请时间</span><strong>{{ formatShanghai(store.approval.requested_at) }}</strong><span>到期时间</span><strong>{{ formatShanghai(store.approval.expires_at) }}</strong></div>
            <button v-if="store.approval.status === 'PENDING'" class="button secondary" :disabled="store.working || !canOperate" @click="store.preflight(store.approval.id)">运行 qB 只读预检</button>
            <div v-if="store.approval.status === 'PENDING'" class="acknowledgements">
              <label><input v-model="acknowledgesHnr" type="checkbox" :disabled="!canAdmin || store.working" />已了解该站 H&R 规则</label>
              <label><input v-model="acknowledgesSeeding" type="checkbox" :disabled="!canAdmin || store.working" />下载完成后需要继续做种</label>
              <label><input v-model="acknowledgesPlanOnly" type="checkbox" :disabled="!canAdmin || store.working" />当前阶段仅创建下载计划，不开始下载</label>
            </div>
            <button
              v-if="store.approval.status === 'PENDING'"
              class="button primary"
              :disabled="store.working || !canAdmin || !acknowledgementsComplete || !preflightPassable"
              @click="store.approve(store.approval.id)"
            >
              批准并生成计划
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
