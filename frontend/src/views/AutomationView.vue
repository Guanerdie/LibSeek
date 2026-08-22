<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useAuthStore } from '../stores/auth'
import {
  useAutomationStore,
  type AutomationPolicyDraft,
} from '../stores/automation'
import type {
  AutomationDecision,
  AutomationDecisionOutcome,
  AutomationMode,
  AutomationPolicyRevision,
  AutomationStage,
} from '../types'
import { formatShanghai } from '../utils/format'

const auth = useAuthStore()
const store = useAutomationStore()
const decisionStage = ref<AutomationStage | ''>('')
const decisionOutcome = ref<AutomationDecisionOutcome | ''>('')
const decisionMediaId = ref('')

function decisionMediaTarget(decision: AutomationDecision): string {
  const suffix = decision.stage === 'IDENTITY' ? 'identity' : 'torrents'
  return `/media/${encodeURIComponent(decision.media_item_id)}/${suffix}`
}

function decisionResultTarget(decision: AutomationDecision): string {
  if (decision.download_execution_id) {
    return `/executions/${encodeURIComponent(decision.download_execution_id)}`
  }
  if (decision.approval_request_id) {
    return `/approvals/${encodeURIComponent(decision.approval_request_id)}`
  }
  return decisionMediaTarget(decision)
}

const modes: Array<{ value: AutomationMode; label: string; description: string }> = [
  { value: 'DISABLED', label: '关闭', description: '禁止该阶段任何新动作' },
  { value: 'MANUAL', label: '人工', description: '仅记录并等待确认' },
  { value: 'AUTO_IF_ELIGIBLE', label: '条件自动', description: '全部规则通过才动作' },
]

const stages: Array<{
  key: keyof Pick<
    AutomationPolicyDraft,
    'identity_mode' | 'torrent_selection_mode' | 'approval_mode' | 'execution_mode'
  >
  code: AutomationStage
  title: string
  description: string
}> = [
  {
    key: 'identity_mode',
    code: 'IDENTITY',
    title: '影视身份确认',
    description: '匹配 TMDB 身份；冲突、低分或低分差始终转人工。',
  },
  {
    key: 'torrent_selection_mode',
    code: 'TORRENT_SELECTION',
    title: 'PT 候选选择',
    description: '按匹配分、分差和做种人数选择；H&R UNKNOWN 阻断自动选择。',
  },
  {
    key: 'approval_mode',
    code: 'APPROVAL',
    title: '审批与计划',
    description: '仅在候选和预检均明确安全时生成不可变下载计划。',
  },
  {
    key: 'execution_mode',
    code: 'EXECUTION',
    title: '下载执行',
    description: '真实写入受执行器三重开关保护，自动动作只允许添加后暂停。',
  },
]

const stageOptions: AutomationStage[] = [
  'IDENTITY',
  'TORRENT_SELECTION',
  'APPROVAL',
  'EXECUTION',
]
const outcomeOptions: AutomationDecisionOutcome[] = [
  'ACTION_CREATED',
  'MANUAL_REQUIRED',
  'BLOCKED',
  'DISABLED',
  'STALE',
  'NOOP',
]

const canAdminister = computed(() => auth.hasRole('admin'))
const approvalAutomatic = computed(() => store.draft.approval_mode === 'AUTO_IF_ELIGIBLE')
const executionAutomatic = computed(() => store.draft.execution_mode === 'AUTO_IF_ELIGIBLE')
const needsSafetyAcknowledgements = computed(
  () => approvalAutomatic.value || executionAutomatic.value,
)
const desiredAcknowledgements = computed(() => ({
  acknowledges_hnr: needsSafetyAcknowledgements.value,
  acknowledges_seeding: needsSafetyAcknowledgements.value,
  acknowledges_plan_only: approvalAutomatic.value,
  acknowledges_add_paused_only: executionAutomatic.value,
}))
const acknowledgementsComplete = computed(() => {
  if (!needsSafetyAcknowledgements.value) return true
  if (!store.draft.acknowledges_hnr || !store.draft.acknowledges_seeding) return false
  if (approvalAutomatic.value && !store.draft.acknowledges_plan_only) return false
  if (executionAutomatic.value && !store.draft.acknowledges_add_paused_only) return false
  return true
})

const numericSettingsValid = computed(
  () =>
    [
      store.draft.identity_min_score,
      store.draft.identity_min_margin,
      store.draft.torrent_min_score,
      store.draft.torrent_min_margin,
    ].every((value) => Number.isFinite(value) && value >= 0 && value <= 1) &&
    Number.isInteger(store.draft.torrent_min_seeders) &&
    store.draft.torrent_min_seeders >= 1 &&
    store.draft.torrent_min_seeders <= 100_000,
)

const policyChanged = computed(() => {
  const revision = store.policy?.revision
  if (!revision) return false
  return (
    revision.identity_mode !== store.draft.identity_mode ||
    revision.torrent_selection_mode !== store.draft.torrent_selection_mode ||
    revision.approval_mode !== store.draft.approval_mode ||
    revision.execution_mode !== store.draft.execution_mode ||
    revision.identity_min_score !== store.draft.identity_min_score ||
    revision.identity_min_margin !== store.draft.identity_min_margin ||
    revision.torrent_min_score !== store.draft.torrent_min_score ||
    revision.torrent_min_margin !== store.draft.torrent_min_margin ||
    revision.torrent_min_seeders !== store.draft.torrent_min_seeders ||
    revision.acknowledges_hnr !== desiredAcknowledgements.value.acknowledges_hnr ||
    revision.acknowledges_seeding !== desiredAcknowledgements.value.acknowledges_seeding ||
    revision.acknowledges_plan_only !== desiredAcknowledgements.value.acknowledges_plan_only ||
    revision.acknowledges_add_paused_only !==
      desiredAcknowledgements.value.acknowledges_add_paused_only
  )
})

const canPublish = computed(
  () =>
    canAdminister.value &&
    Boolean(store.policy) &&
    policyChanged.value &&
    numericSettingsValid.value &&
    acknowledgementsComplete.value &&
    !store.policyLoading &&
    !store.publishing,
)

onMounted(() => {
  void Promise.all([store.loadPolicy(), store.loadRevisions(), store.loadDecisions()])
})

function setMode(stage: (typeof stages)[number], mode: AutomationMode): void {
  if (!canAdminister.value || store.publishing) return
  store.draft[stage.key] = mode
}

function loadDecisions(page = 1): void {
  void store.loadDecisions(
    {
      stage: decisionStage.value || undefined,
      outcome: decisionOutcome.value || undefined,
      mediaItemId: decisionMediaId.value.trim() || undefined,
    },
    page,
  )
}

function resetDecisionFilters(): void {
  decisionStage.value = ''
  decisionOutcome.value = ''
  decisionMediaId.value = ''
  loadDecisions(1)
}

function shortHash(value: string | null): string {
  if (!value) return '创世修订'
  return `${value.slice(0, 12)}…${value.slice(-8)}`
}

function stageLabel(stage: AutomationStage): string {
  return stages.find((item) => item.code === stage)?.title ?? stage
}

function modeLabel(mode: AutomationMode): string {
  return modes.find((item) => item.value === mode)?.label ?? mode
}

function revisionModeSummary(revision: AutomationPolicyRevision): string {
  return [
    revision.identity_mode,
    revision.torrent_selection_mode,
    revision.approval_mode,
    revision.execution_mode,
  ]
    .map(modeLabel)
    .join(' / ')
}
</script>

<template>
  <section class="page wide-page automation-page">
    <PageHeader
      eyebrow="CONTROLLED AUTOMATION"
      title="自动化策略与决策"
      description="按阶段启用保守自动化；策略不可变、决策可审计，真实下载仍受服务端执行开关保护。"
    >
      <div class="header-actions">
        <button class="button secondary" :disabled="store.policyLoading || store.publishing" @click="store.loadPolicy()">
          刷新策略
        </button>
        <button class="button secondary" :disabled="store.decisionsLoading" @click="loadDecisions(store.decisionsPage)">
          刷新决策
        </button>
      </div>
    </PageHeader>

    <div class="phase-banner automation-banner">
      <span>保守默认</span>身份、选种、审批和执行默认均为人工；H&amp;R UNKNOWN 会阻断自动选种、审批和执行。
    </div>

    <PageState :loading="store.policyLoading" :error="store.policyError" />

    <template v-if="store.policy && !store.policyLoading && !store.policyError">
      <div class="automation-engine-strip" :class="{ disabled: !store.policy.engine_enabled }">
        <div>
          <span class="eyebrow">ENGINE GATE</span>
          <strong>{{ store.policy.engine_enabled ? '自动化引擎已启用' : '自动化引擎已关闭' }}</strong>
          <small>
            {{ store.policy.engine_enabled
              ? '策略只在全部资格规则通过时生效。'
              : '策略可配置和审计，但服务端不会创建自动动作。' }}
          </small>
        </div>
        <StatusPill :status="store.policy.engine_enabled ? 'ENABLED' : 'DISABLED'" />
        <dl>
          <div><dt>策略头版本</dt><dd>{{ store.policy.version }}</dd></div>
          <div><dt>当前修订</dt><dd>#{{ store.policy.revision.revision_no }}</dd></div>
          <div><dt>生效时间</dt><dd>{{ formatShanghai(store.policy.revision.effective_from) }}</dd></div>
        </dl>
      </div>

      <div class="automation-layout">
        <div class="automation-policy-main">
          <div class="automation-section-heading">
            <div>
              <span class="eyebrow">STAGE MODES</span>
              <h2>四阶段模式</h2>
              <p>viewer 与 operator 可查看；只有 admin 能创建新的不可变修订。</p>
            </div>
            <span class="permission-hint">{{ canAdminister ? '管理员编辑模式' : '当前角色只读' }}</span>
          </div>

          <div class="automation-stage-grid">
            <article v-for="stage in stages" :key="stage.key" class="automation-stage-card">
              <header>
                <span>{{ stage.code }}</span>
                <h3>{{ stage.title }}</h3>
                <p>{{ stage.description }}</p>
              </header>
              <div class="automation-mode-control" :aria-label="`${stage.title}模式`">
                <button
                  v-for="mode in modes"
                  :key="mode.value"
                  type="button"
                  :class="{ active: store.draft[stage.key] === mode.value }"
                  :disabled="!canAdminister || store.publishing"
                  :aria-pressed="store.draft[stage.key] === mode.value"
                  @click="setMode(stage, mode.value)"
                >
                  <strong>{{ mode.label }}</strong>
                  <small>{{ mode.description }}</small>
                </button>
              </div>
            </article>
          </div>

          <section class="automation-thresholds">
            <div class="automation-section-heading compact">
              <div><span class="eyebrow">ELIGIBILITY</span><h2>资格阈值</h2></div>
              <small>分数与分差范围为 0–1；任一不满足都会转人工或阻断。</small>
            </div>
            <div class="threshold-grid">
              <label>身份最低分<input v-model.number="store.draft.identity_min_score" type="number" min="0" max="1" step="0.01" :disabled="!canAdminister" /></label>
              <label>身份最低分差<input v-model.number="store.draft.identity_min_margin" type="number" min="0" max="1" step="0.01" :disabled="!canAdminister" /></label>
              <label>种子最低分<input v-model.number="store.draft.torrent_min_score" type="number" min="0" max="1" step="0.01" :disabled="!canAdminister" /></label>
              <label>种子最低分差<input v-model.number="store.draft.torrent_min_margin" type="number" min="0" max="1" step="0.01" :disabled="!canAdminister" /></label>
              <label>最低做种人数<input v-model.number="store.draft.torrent_min_seeders" type="number" min="1" max="100000" step="1" :disabled="!canAdminister" /></label>
            </div>
            <p v-if="!numericSettingsValid" class="field-error" role="alert">阈值格式无效：分数须为 0–1，最低做种人数须为 1–100000 的整数。</p>
          </section>
        </div>

        <aside class="automation-publish-panel">
          <span class="eyebrow">NEW REVISION</span>
          <h2>发布策略修订</h2>
          <p>发布以当前 #{{ store.policy.revision.revision_no }} 为基线；并发更新会被拒绝，不会覆盖他人策略。</p>

          <div class="hash-chain-summary">
            <div><span>当前策略哈希</span><code :title="store.policy.revision.policy_hash">{{ shortHash(store.policy.revision.policy_hash) }}</code></div>
            <div><span>前序策略哈希</span><code :title="store.policy.revision.previous_policy_hash ?? ''">{{ shortHash(store.policy.revision.previous_policy_hash) }}</code></div>
          </div>

          <div v-if="needsSafetyAcknowledgements" class="automation-acknowledgements">
            <strong>自动动作安全确认</strong>
            <label>
              <input v-model="store.draft.acknowledges_hnr" type="checkbox" :disabled="!canAdminister" />
              <span>我确认 H&amp;R 为 UNKNOWN 时必须阻断自动动作。</span>
            </label>
            <label>
              <input v-model="store.draft.acknowledges_seeding" type="checkbox" :disabled="!canAdminister" />
              <span>我确认下载后仍需遵守站点继续做种义务。</span>
            </label>
            <label v-if="approvalAutomatic">
              <input v-model="store.draft.acknowledges_plan_only" type="checkbox" :disabled="!canAdminister" />
              <span>我确认自动审批只生成不可变计划，不代表下载已提交。</span>
            </label>
            <label v-if="executionAutomatic">
              <input v-model="store.draft.acknowledges_add_paused_only" type="checkbox" :disabled="!canAdminister" />
              <span>我确认自动执行会真实添加任务，并且只允许添加后暂停或交由 qB 队列调度。</span>
            </label>
          </div>
          <div v-else class="automation-safe-note">当前未启用自动审批或自动执行，无需高风险确认。</div>

          <div v-if="store.publishConflict" class="notice-state warning-state publish-message" role="alert">
            {{ store.publishConflict }}
            <button class="button small secondary" @click="store.loadPolicy()">刷新当前修订</button>
          </div>
          <div v-if="store.publishError" class="notice-state error-state publish-message" role="alert">{{ store.publishError }}</div>
          <div v-if="store.notice" class="notice-state success-state publish-message" role="status">{{ store.notice }}</div>

          <button class="button primary publish-button" :disabled="!canPublish" @click="store.publish()">
            {{ store.publishing ? '发布中…' : '发布新修订' }}
          </button>
          <button class="button secondary publish-button" :disabled="store.publishing" @click="store.resetPublicationState()">放弃未发布修改</button>
          <small v-if="!canAdminister" class="permission-hint">viewer / operator 无发布权限。</small>
          <small v-else-if="!policyChanged">修改至少一个模式或阈值后才能发布。</small>
          <small v-else-if="!acknowledgementsComplete">请完成当前自动动作要求的全部确认。</small>
          <small>确认状态仅保存在当前页面内存中，不写入浏览器存储。</small>
        </aside>
      </div>
    </template>

    <section class="automation-history-section">
      <div class="automation-section-heading">
        <div><span class="eyebrow">HASH CHAIN</span><h2>策略修订历史</h2><p>每次发布生成新记录；哈希链用于发现历史策略被篡改或断链。</p></div>
      </div>
      <PageState
        :loading="store.revisionsLoading"
        :error="store.revisionsError"
        :empty="!store.revisionsLoading && !store.revisionsError && store.revisions.length === 0"
        empty-text="暂无策略修订"
      />
      <div v-if="store.revisions.length" class="candidate-table-wrap">
        <table class="automation-revision-table">
          <thead><tr><th>修订</th><th>四阶段模式</th><th>策略哈希</th><th>前序哈希</th><th>发布人</th><th>生效时间</th></tr></thead>
          <tbody>
            <tr v-for="revision in store.revisions" :key="revision.id">
              <td><strong>#{{ revision.revision_no }}</strong><small class="mono">{{ revision.id }}</small></td>
              <td><strong>{{ revisionModeSummary(revision) }}</strong><small>批次可选择添加后暂停或 qB 队列调度</small></td>
              <td><code :title="revision.policy_hash">{{ shortHash(revision.policy_hash) }}</code></td>
              <td><code :title="revision.previous_policy_hash ?? ''">{{ shortHash(revision.previous_policy_hash) }}</code></td>
              <td>{{ revision.created_by }}</td>
              <td>{{ formatShanghai(revision.effective_from) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="store.revisionsTotal > 0 || store.revisionsPage > 1" class="pagination standalone-pagination">
        <span>共 {{ store.revisionsTotal }} 个修订 · 第 {{ store.revisionsPage }} 页</span>
        <div>
          <button class="button small" :disabled="store.revisionsPage <= 1" @click="store.loadRevisions(store.revisionsPage - 1)">上一页</button>
          <button class="button small" :disabled="store.revisionsPage * store.revisionsPageSize >= store.revisionsTotal" @click="store.loadRevisions(store.revisionsPage + 1)">下一页</button>
        </div>
      </div>
    </section>

    <section class="automation-decisions-section">
      <div class="automation-section-heading">
        <div><span class="eyebrow">APPEND-ONLY AUDIT</span><h2>自动化决策</h2><p>展示每次资格判断、创建的动作以及明确的阻断原因。</p></div>
      </div>
      <form class="filter-bar automation-decision-filters" @submit.prevent="loadDecisions(1)">
        <select v-model="decisionStage" aria-label="决策阶段"><option value="">全部阶段</option><option v-for="stage in stageOptions" :key="stage" :value="stage">{{ stageLabel(stage) }}</option></select>
        <select v-model="decisionOutcome" aria-label="决策结果"><option value="">全部结果</option><option v-for="outcome in outcomeOptions" :key="outcome" :value="outcome">{{ outcome }}</option></select>
        <input v-model="decisionMediaId" aria-label="影视 ID" maxlength="36" placeholder="按 media_item_id 精确过滤" />
        <button class="button" type="submit" :disabled="store.decisionsLoading">应用筛选</button>
        <button class="button secondary" type="button" :disabled="store.decisionsLoading" @click="resetDecisionFilters">重置</button>
      </form>

      <PageState
        :loading="store.decisionsLoading"
        :error="store.decisionsError"
        :empty="!store.decisionsLoading && !store.decisionsError && store.decisions.length === 0"
        empty-text="当前筛选下没有自动化决策"
      />
      <div v-if="store.decisions.length" class="candidate-table-wrap">
        <table class="automation-decision-table">
          <thead><tr><th>阶段 / 动作</th><th>结果</th><th>影视</th><th>阻断或判断原因</th><th>证据哈希</th><th>时间</th><th>详情</th></tr></thead>
          <tbody>
            <tr v-for="decision in store.decisions" :key="decision.id" :class="{ 'blocked-decision': decision.outcome === 'BLOCKED' }">
              <td><strong>{{ stageLabel(decision.stage) }}</strong><small>{{ decision.action }}</small></td>
              <td><StatusPill :status="decision.outcome" /></td>
              <td><a class="inline-record-link mono" :href="decisionMediaTarget(decision)">{{ decision.media_item_id }}</a><small class="mono">{{ decision.id }}</small></td>
              <td><div class="decision-reasons"><span v-for="reason in decision.reason_codes" :key="reason">{{ reason }}</span><small v-if="decision.reason_codes.length === 0">无附加原因</small></div></td>
              <td><code :title="decision.evidence_hash">{{ shortHash(decision.evidence_hash) }}</code></td>
              <td><strong>{{ formatShanghai(decision.created_at) }}</strong><small>{{ decision.actor }}</small></td>
              <td><div class="row-actions"><button class="table-action" type="button" @click="store.loadDecision(decision.id)">查看证据</button><a class="table-action" :href="decisionResultTarget(decision)">进入结果</a></div></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="store.decisionsTotal > 0 || store.decisionsPage > 1" class="pagination standalone-pagination">
        <span>共 {{ store.decisionsTotal }} 条决策 · 第 {{ store.decisionsPage }} 页</span>
        <div>
          <button class="button small" :disabled="store.decisionsPage <= 1" @click="loadDecisions(store.decisionsPage - 1)">上一页</button>
          <button class="button small" :disabled="store.decisionsPage * store.decisionsPageSize >= store.decisionsTotal" @click="loadDecisions(store.decisionsPage + 1)">下一页</button>
        </div>
      </div>

      <PageState :loading="store.decisionLoading" :error="store.decisionError" />
      <article v-if="store.selectedDecision && !store.decisionLoading" class="automation-decision-detail">
        <header>
          <div><span class="eyebrow">DECISION EVIDENCE</span><h3>{{ stageLabel(store.selectedDecision.stage) }} · {{ store.selectedDecision.action }}</h3></div>
          <StatusPill :status="store.selectedDecision.outcome" />
        </header>
        <dl>
          <div><dt>决策 ID</dt><dd class="mono">{{ store.selectedDecision.id }}</dd></div>
          <div><dt>策略修订 ID</dt><dd class="mono">{{ store.selectedDecision.policy_revision_id }}</dd></div>
          <div><dt>影视 ID</dt><dd><a class="inline-record-link mono" :href="decisionMediaTarget(store.selectedDecision)">{{ store.selectedDecision.media_item_id }}</a></dd></div>
          <div><dt>元数据候选</dt><dd class="mono">{{ store.selectedDecision.metadata_match_id ?? '—' }}</dd></div>
          <div><dt>种子候选</dt><dd class="mono">{{ store.selectedDecision.torrent_candidate_id ?? '—' }}</dd></div>
          <div><dt>审批请求</dt><dd><a v-if="store.selectedDecision.approval_request_id" class="inline-record-link mono" :href="`/approvals/${store.selectedDecision.approval_request_id}`">{{ store.selectedDecision.approval_request_id }}</a><span v-else>—</span></dd></div>
          <div><dt>下载执行</dt><dd><a v-if="store.selectedDecision.download_execution_id" class="inline-record-link mono" :href="`/executions/${store.selectedDecision.download_execution_id}`">{{ store.selectedDecision.download_execution_id }}</a><span v-else>—</span></dd></div>
          <div><dt>决策主体</dt><dd>{{ store.selectedDecision.actor }}</dd></div>
          <div><dt>证据哈希</dt><dd class="mono">{{ store.selectedDecision.evidence_hash }}</dd></div>
        </dl>
        <div class="decision-detail-reasons"><strong>原因代码</strong><span v-for="reason in store.selectedDecision.reason_codes" :key="reason">{{ reason }}</span><small v-if="store.selectedDecision.reason_codes.length === 0">无附加原因</small></div>
        <div class="decision-evidence"><strong>脱敏证据快照</strong><pre>{{ JSON.stringify(store.selectedDecision.evidence_snapshot, null, 2) }}</pre></div>
      </article>
    </section>
  </section>
</template>
