<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useApprovalStore } from '../stores/approvals'
import { useAuthStore } from '../stores/auth'
import type { ApprovalCandidateSnapshot } from '../types'
import { formatShanghai } from '../utils/format'

const route = useRoute()
const auth = useAuthStore()
const store = useApprovalStore()
const ttl = ref(60)
const reason = ref('')
const acknowledgesHnr = ref(false)
const acknowledgesSeeding = ref(false)
const acknowledgesPlanOnly = ref(false)

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

onMounted(() => {
  const approvalId = route.params.id
  if (approvalId) {
    void store.loadApproval(String(approvalId))
    return
  }
  void store.loadCandidate(
    String(route.params.mediaId),
    String(route.params.searchId),
    String(route.params.candidateId),
  )
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

function createApproval(): void {
  if (!snapshot.value || !canOperate.value) return
  void store.create(snapshot.value.torrent_candidate_id, ttl.value)
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
    <div class="phase-banner"><span>禁止执行</span>当前阶段只生成下载计划，不获取 .torrent，不向 qBittorrent 添加任务。</div>
    <PageState :loading="store.loading" :error="store.error" />
    <div v-if="store.notice" class="notice-state">{{ store.notice }}</div>
    <div v-if="store.planError" class="notice-state warning-state">{{ store.planError }}</div>

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
            <span class="eyebrow">NON-EXECUTABLE PLAN</span><h2>下载计划</h2>
            <dl class="approval-facts">
              <div><dt>内部种子引用</dt><dd class="mono">{{ store.plan.torrent_ref }}</dd></div>
              <div><dt>预期 info_hash</dt><dd class="mono">{{ store.plan.expected_info_hash ?? '未知' }}</dd></div>
              <div><dt>保存位置引用</dt><dd>{{ store.plan.save_path_ref }}</dd></div>
              <div><dt>分类 / 标签</dt><dd>{{ store.plan.category }} / {{ store.plan.tags.join(', ') || '—' }}</dd></div>
              <div><dt>预计大小</dt><dd>{{ formatBytes(store.plan.estimated_size_bytes) }}</dd></div>
              <div><dt>模式</dt><dd>PLAN_ONLY_NO_FILE_OPERATION</dd></div>
            </dl>
            <div class="phase-banner inline-banner"><span>未执行</span>没有真实下载 URL，也没有向 qBittorrent 提交任何内容。</div>
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
