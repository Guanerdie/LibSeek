<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useAuthStore } from '../stores/auth'
import { useMediaImportStore } from '../stores/mediaImports'
import type { MediaImportEvent, MediaImportOperation } from '../types'
import { formatShanghai } from '../utils/format'

const route = useRoute()
const auth = useAuthStore()
const store = useMediaImportStore()
const acknowledgements = reactive({
  planOnly: false,
  sourceRetention: false,
  noOverwrite: false,
  hnr: false,
})
const reason = ref('')
const decisionConfirmed = ref(false)
const requestItem = computed(() =>
  store.selected?.id === String(route.params.id) ? store.selected : null,
)
const jobSummary = computed(() => requestItem.value?.plan.job_summary_snapshot ?? null)
const hnrNeedsAcknowledgement = computed(() => jobSummary.value?.hnr_status !== 'SATISFIED')
const targetBySource = computed(
  () =>
    new Map(
      requestItem.value?.plan.target_mapping.files.map((entry) => [
        entry.source_relative_path,
        entry.target_relative_path,
      ]) ?? [],
    ),
)
const canApprove = computed(
  () =>
    auth.hasRole('admin') &&
    requestItem.value?.status === 'REVIEW_REQUIRED' &&
    acknowledgements.planOnly &&
    acknowledgements.sourceRetention &&
    acknowledgements.noOverwrite &&
    (!hnrNeedsAcknowledgement.value || acknowledgements.hnr) &&
    !store.working,
)

watch(
  () => String(route.params.id),
  (requestId) => {
    resetDecisionState()
    void store.loadDetail(requestId)
  },
  { immediate: true },
)

function resetDecisionState(): void {
  acknowledgements.planOnly = false
  acknowledgements.sourceRetention = false
  acknowledgements.noOverwrite = false
  acknowledgements.hnr = false
  reason.value = ''
  decisionConfirmed.value = false
}

function refresh(): void {
  resetDecisionState()
  void store.loadDetail(String(route.params.id))
}

async function approve(): Promise<void> {
  if (!requestItem.value || !canApprove.value) return
  const requestId = requestItem.value.id
  const succeeded = await store.approve(requestId, {
    acknowledges_plan_only: acknowledgements.planOnly,
    acknowledges_source_retention: acknowledgements.sourceRetention,
    acknowledges_no_overwrite: acknowledgements.noOverwrite,
    acknowledges_hnr: hnrNeedsAcknowledgement.value ? acknowledgements.hnr : false,
  })
  if (succeeded && requestItem.value?.id === requestId) resetDecisionState()
}

async function reject(): Promise<void> {
  if (!requestItem.value || !auth.hasRole('admin') || !decisionConfirmed.value) return
  const requestId = requestItem.value.id
  const succeeded = await store.reject(requestId, reason.value)
  if (succeeded && requestItem.value?.id === requestId) resetDecisionState()
}

async function revoke(): Promise<void> {
  if (!requestItem.value || !auth.hasRole('admin') || !decisionConfirmed.value) return
  const requestId = requestItem.value.id
  const succeeded = await store.revoke(requestId, reason.value)
  if (succeeded && requestItem.value?.id === requestId) resetDecisionState()
}

function operationLabel(value: MediaImportOperation): string {
  return value === 'HARDLINK' ? '硬链接（HARDLINK）' : '复制（COPY）'
}

function formatBytes(value: number): string {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`
}

function transitionLabel(item: MediaImportEvent): string {
  return item.from_status ? `${item.from_status} → ${item.to_status}` : item.to_status
}

function detailsLabel(details: Record<string, unknown>): string {
  return Object.keys(details).length ? JSON.stringify(details, null, 2) : ''
}

function targetFor(sourcePath: string): string | null {
  return targetBySource.value.get(sourcePath) ?? null
}
</script>

<template>
  <section class="page wide-page media-import-page">
    <PageHeader
      eyebrow="MEDIA IMPORT PLAN DETAIL"
      title="入库规划详情"
      description="核对不可变计划、只读预检和人工决策记录。"
    >
      <div class="header-actions">
        <RouterLink class="button secondary" to="/media-imports">返回规划列表</RouterLink>
        <button class="button secondary" :disabled="store.detailLoading" @click="refresh">刷新详情</button>
      </div>
    </PageHeader>

    <div class="phase-banner media-import-safety"><span>仅规划</span>仅规划，不操作媒体文件</div>
    <PageState :loading="store.detailLoading" :error="store.detailError" />

    <template v-if="requestItem && jobSummary && !store.detailLoading && !store.detailError">
      <section class="source-strip media-import-source-strip">
        <div><span>媒体</span><strong>{{ jobSummary.media_title }}</strong></div>
        <div><span>规划状态</span><StatusPill :status="requestItem.status" /></div>
        <div><span>建议方式</span><strong>{{ operationLabel(requestItem.plan.proposed_operation) }}</strong></div>
        <div><span>预检</span><StatusPill v-if="requestItem.preflight" :status="requestItem.preflight.overall_status" /><strong v-else>待内部预检</strong></div>
        <div><span>申请时间</span><strong>{{ formatShanghai(requestItem.requested_at) }}</strong></div>
      </section>

      <div v-if="!requestItem.preflight" class="notice-state warning-state">
        尚无受信只读预检记录。预检由内部只读边界写入，当前页面不会触发目录扫描。
      </div>
      <div v-if="jobSummary.hnr_status !== 'SATISFIED'" class="notice-state warning-state">
        H&amp;R 为 {{ jobSummary.hnr_status }}，批准规划前需要管理员显式确认；批准也不会执行文件操作。
      </div>

      <div class="media-import-detail-grid">
        <section class="media-import-detail-section">
          <span class="eyebrow">DOWNLOAD SUMMARY</span><h2>下载与媒体绑定</h2>
          <dl class="approval-facts media-import-facts">
            <div><dt>媒体标题</dt><dd>{{ jobSummary.media_title }}</dd></div>
            <div><dt>类型 / 年份</dt><dd>{{ jobSummary.media_type === 'tv' ? '剧集' : '电影' }} · {{ jobSummary.media_year ?? '—' }}</dd></div>
            <div><dt>TMDB ID</dt><dd>{{ jobSummary.tmdb_id ?? '—' }}</dd></div>
            <div><dt>下载状态</dt><dd><StatusPill :status="jobSummary.job_status" /></dd></div>
            <div><dt>执行状态</dt><dd><StatusPill :status="jobSummary.execution_status" /></dd></div>
            <div><dt>H&amp;R</dt><dd :class="jobSummary.hnr_status === 'SATISFIED' ? '' : 'risk-text'">{{ jobSummary.hnr_status }}</dd></div>
            <div><dt>大小 / 文件</dt><dd>{{ formatBytes(jobSummary.size_bytes) }} · {{ jobSummary.file_count }} 个</dd></div>
            <div><dt>下载任务</dt><dd><RouterLink class="inline-record-link" :to="`/download-jobs/${jobSummary.download_job_id}`">{{ jobSummary.download_job_id }}</RouterLink></dd></div>
          </dl>
        </section>

        <section class="media-import-detail-section">
          <span class="eyebrow">PLAN BOUNDARY</span><h2>计划约束</h2>
          <dl class="approval-facts media-import-facts">
            <div><dt>模式</dt><dd class="mono">{{ requestItem.plan.mode }}</dd></div>
            <div><dt>建议方式</dt><dd>{{ operationLabel(requestItem.plan.proposed_operation) }}</dd></div>
            <div><dt>源根引用</dt><dd class="mono">{{ requestItem.plan.source_manifest.source_root_ref }}</dd></div>
            <div><dt>目标根引用</dt><dd class="mono">{{ requestItem.plan.target_mapping.target_root_ref }}</dd></div>
            <div><dt>保留源文件</dt><dd class="safe-value">是</dd></div>
            <div><dt>允许覆盖</dt><dd class="safe-value">否</dd></div>
            <div><dt>创建者</dt><dd>{{ requestItem.plan.created_by }}</dd></div>
            <div><dt>创建时间</dt><dd>{{ formatShanghai(requestItem.plan.created_at) }}</dd></div>
          </dl>
        </section>

        <section class="media-import-detail-section media-import-wide-section">
          <div class="media-import-section-heading">
            <div><span class="eyebrow">FILE MAPPING</span><h2>文件清单与目标映射</h2></div>
            <small>{{ requestItem.plan.source_manifest.files.length }} 个文件 · 不覆盖目标</small>
          </div>
          <div class="candidate-table-wrap media-import-desktop-list">
            <table class="media-import-mapping-table">
              <thead><tr><th>源相对路径</th><th>大小</th><th>目标相对路径</th></tr></thead>
              <tbody>
                <tr v-for="file in requestItem.plan.source_manifest.files" :key="file.relative_path">
                  <td class="mono">{{ file.relative_path }}</td>
                  <td>{{ formatBytes(file.size_bytes) }}</td>
                  <td :class="targetFor(file.relative_path) ? 'mono' : 'unmapped-file'">{{ targetFor(file.relative_path) ?? '未加入目标映射' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="media-import-mobile-list mapping-mobile-list">
            <article v-for="file in requestItem.plan.source_manifest.files" :key="file.relative_path" class="media-import-mobile-item">
              <strong class="mono">{{ file.relative_path }}</strong>
              <span class="mapping-arrow">↓</span>
              <strong :class="targetFor(file.relative_path) ? 'mono' : 'unmapped-file'">{{ targetFor(file.relative_path) ?? '未加入目标映射' }}</strong>
              <small>{{ formatBytes(file.size_bytes) }}</small>
            </article>
          </div>
        </section>

        <section class="media-import-detail-section media-import-wide-section">
          <div class="media-import-section-heading">
            <div><span class="eyebrow">READ-ONLY PREFLIGHT</span><h2>受信预检</h2></div>
            <StatusPill v-if="requestItem.preflight" :status="requestItem.preflight.overall_status" />
          </div>
          <template v-if="requestItem.preflight">
            <div class="preflight-check-grid">
              <article v-for="check in requestItem.preflight.result.checks" :key="check.code">
                <div><strong>{{ check.code }}</strong><StatusPill :status="check.status" /></div>
                <p>{{ check.message }}</p>
              </article>
            </div>
            <small class="preflight-meta">{{ requestItem.preflight.checked_by }} · {{ formatShanghai(requestItem.preflight.checked_at) }}</small>
          </template>
          <p v-else class="empty-inline">等待内部只读检查记录；本页面没有扫描入口。</p>
        </section>

        <section class="media-import-detail-section media-import-wide-section decision-section">
          <div class="media-import-section-heading">
            <div><span class="eyebrow">MANUAL DECISION</span><h2>人工决策</h2></div>
            <small v-if="!auth.hasRole('admin')" class="permission-hint">当前角色只读；仅管理员可以决策。</small>
          </div>

          <div v-if="requestItem.status === 'REVIEW_REQUIRED'" class="plan-approval-controls">
            <div class="plan-acknowledgements">
              <strong>批准确认</strong>
              <label><input v-model="acknowledgements.planOnly" type="checkbox" :disabled="!auth.hasRole('admin') || store.working" /> 我确认批准后仅生成已批准计划，不授权执行</label>
              <label><input v-model="acknowledgements.sourceRetention" type="checkbox" :disabled="!auth.hasRole('admin') || store.working" /> 我确认下载源文件必须保留</label>
              <label><input v-model="acknowledgements.noOverwrite" type="checkbox" :disabled="!auth.hasRole('admin') || store.working" /> 我确认禁止覆盖任何目标文件</label>
              <label v-if="hnrNeedsAcknowledgement"><input v-model="acknowledgements.hnr" type="checkbox" :disabled="!auth.hasRole('admin') || store.working" /> 我确认 H&amp;R 尚未由后端记录为 SATISFIED</label>
            </div>
            <button class="button primary" :disabled="!canApprove" @click="approve">{{ store.working ? '处理中…' : '批准纯规划' }}</button>
          </div>

          <div v-if="requestItem.status === 'PREFLIGHT_REQUIRED' || requestItem.status === 'REVIEW_REQUIRED' || requestItem.status === 'APPROVED_PLAN_ONLY'" class="plan-terminal-action">
            <label><span>{{ requestItem.status === 'APPROVED_PLAN_ONLY' ? '撤销原因' : '拒绝原因' }}</span><input v-model="reason" :aria-label="requestItem.status === 'APPROVED_PLAN_ONLY' ? '撤销原因' : '拒绝原因'" maxlength="500" :disabled="!auth.hasRole('admin') || store.working" /></label>
            <label class="terminal-confirm"><input v-model="decisionConfirmed" type="checkbox" :disabled="!auth.hasRole('admin') || store.working" /> 我确认只更改规划状态，不执行媒体文件操作</label>
            <button v-if="requestItem.status === 'APPROVED_PLAN_ONLY'" class="button danger-button" :disabled="!auth.hasRole('admin') || !decisionConfirmed || store.working" @click="revoke">撤销规划</button>
            <button v-else class="button danger-button" :disabled="!auth.hasRole('admin') || !decisionConfirmed || store.working" @click="reject">拒绝规划</button>
          </div>

          <p v-if="!['PREFLIGHT_REQUIRED', 'REVIEW_REQUIRED', 'APPROVED_PLAN_ONLY'].includes(requestItem.status)" class="empty-inline">该规划已结束，不再接受决策。</p>
          <div v-if="store.actionError" class="notice-state error-state" role="alert">{{ store.actionError }}</div>
        </section>

        <section class="media-import-detail-section media-import-wide-section">
          <span class="eyebrow">INTEGRITY</span><h2>不可变摘要</h2>
          <dl class="integrity-grid">
            <div><dt>Plan hash</dt><dd class="mono">{{ requestItem.plan.plan_hash }}</dd></div>
            <div><dt>Source manifest</dt><dd class="mono">{{ requestItem.plan.source_manifest_hash }}</dd></div>
            <div><dt>Target mapping</dt><dd class="mono">{{ requestItem.plan.target_mapping_hash }}</dd></div>
            <div><dt>Job summary</dt><dd class="mono">{{ requestItem.plan.summary_snapshot_hash }}</dd></div>
            <div><dt>Config fingerprint</dt><dd class="mono">{{ requestItem.plan.config_fingerprint }}</dd></div>
            <div v-if="requestItem.preflight"><dt>Preflight hash</dt><dd class="mono">{{ requestItem.preflight.preflight_hash }}</dd></div>
          </dl>
        </section>

        <section class="media-import-detail-section media-import-wide-section">
          <span class="eyebrow">AUDIT TIMELINE</span><h2>规划时间线</h2>
          <div v-if="requestItem.events.length" class="timeline media-import-timeline">
            <article v-for="event in requestItem.events" :key="event.id">
              <span class="timeline-dot"></span>
              <div>
                <div class="media-import-event-heading"><strong>{{ event.event_type }}</strong><StatusPill :status="event.to_status" /></div>
                <small>{{ transitionLabel(event) }} · {{ event.actor }} · {{ formatShanghai(event.created_at) }}</small>
                <pre v-if="detailsLabel(event.sanitized_details)">{{ detailsLabel(event.sanitized_details) }}</pre>
              </div>
            </article>
          </div>
          <p v-else class="empty-inline">暂无规划事件。</p>
        </section>
      </div>
    </template>
  </section>
</template>
