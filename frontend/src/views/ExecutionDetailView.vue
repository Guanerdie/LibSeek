<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useAuthStore } from '../stores/auth'
import { useExecutionStore } from '../stores/executions'
import { formatShanghai } from '../utils/format'

const route = useRoute()
const auth = useAuthStore()
const store = useExecutionStore()
const reason = ref('')
const canAdmin = computed(() => auth.hasRole('admin'))
const canRequestReconciliation = computed(
  () => store.selected?.requires_reconciliation === true && store.selected.status !== 'RECONCILIATION_PENDING',
)

watch(
  () => String(route.params.id),
  (executionId) => {
    reason.value = ''
    void store.load(executionId)
  },
  { immediate: true },
)

async function reconcile(): Promise<void> {
  if (!store.selected || !canRequestReconciliation.value || !canAdmin.value) return
  if (await store.reconcile(store.selected.id, reason.value)) reason.value = ''
}

function formatBytes(value: number | null): string {
  if (value === null) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`
}
</script>

<template>
  <section class="page wide-page">
    <PageHeader
      eyebrow="EXECUTION DETAIL"
      title="执行详情"
      description="执行状态来自服务端记录；结果未知时必须人工对账，不能直接重试写入。"
    >
      <RouterLink class="button secondary" to="/executions">返回执行列表</RouterLink>
    </PageHeader>

    <PageState :loading="store.loading" :error="store.selected ? null : store.error" />
    <div v-if="store.selected && store.error" class="notice-state warning-state">{{ store.error }}</div>
    <div v-if="store.notice" class="notice-state">{{ store.notice }}</div>

    <template v-if="store.selected && !store.loading">
      <section class="source-strip execution-source">
        <div><span>执行 ID</span><strong class="mono">{{ store.selected.id }}</strong></div>
        <div><span>状态</span><StatusPill :status="store.selected.status" /></div>
        <div><span>启动模式</span><strong>{{ store.selected.launch_mode === 'ADD_PAUSED' ? '添加后暂停' : '立即开始' }}</strong></div>
        <div><span>尝试次数</span><strong>{{ store.selected.attempts }} / {{ store.selected.max_attempts }}</strong></div>
        <div><span>对账</span><strong :class="store.selected.requires_reconciliation ? 'risk-text' : ''">{{ store.selected.requires_reconciliation ? '需要人工处理' : '无需对账' }}</strong></div>
      </section>

      <div class="execution-detail-layout">
        <div class="execution-detail-main">
          <section class="approval-section">
            <span class="eyebrow">INTEGRITY</span><h2>计划与目标指纹</h2>
            <dl class="approval-facts execution-facts">
              <div><dt>审批 ID</dt><dd class="mono">{{ store.selected.approval_id }}</dd></div>
              <div><dt>Intent ID</dt><dd class="mono">{{ store.selected.intent_id }}</dd></div>
              <div><dt>审批快照</dt><dd class="mono">{{ store.selected.approval_snapshot_hash }}</dd></div>
              <div><dt>计划哈希</dt><dd class="mono">{{ store.selected.plan_hash }}</dd></div>
              <div><dt>qB 目标指纹</dt><dd class="mono">{{ store.selected.qb_target_fingerprint }}</dd></div>
              <div><dt>实际 info hash</dt><dd class="mono">{{ store.selected.actual_info_hash || '尚未确认' }}</dd></div>
              <div><dt>实际 v1 / v2</dt><dd class="mono">{{ store.selected.actual_info_hash_v1 || '—' }} / {{ store.selected.actual_info_hash_v2 || '—' }}</dd></div>
              <div><dt>实际大小 / 文件</dt><dd>{{ formatBytes(store.selected.actual_size_bytes) }} / {{ store.selected.actual_file_count ?? '—' }}</dd></div>
            </dl>
          </section>

          <section class="approval-section">
            <span class="eyebrow">TIMELINE</span><h2>执行时间</h2>
            <dl class="approval-facts execution-facts">
              <div><dt>请求</dt><dd>{{ formatShanghai(store.selected.requested_at) }} · {{ store.selected.requested_by }}</dd></div>
              <div><dt>下一次重试</dt><dd>{{ formatShanghai(store.selected.next_retry_at) }}</dd></div>
              <div><dt>校验完成</dt><dd>{{ formatShanghai(store.selected.validated_at) }}</dd></div>
              <div><dt>提交完成</dt><dd>{{ formatShanghai(store.selected.submitted_at) }}</dd></div>
              <div><dt>结果验证</dt><dd>{{ formatShanghai(store.selected.verified_at) }}</dd></div>
              <div><dt>更新时间</dt><dd>{{ formatShanghai(store.selected.updated_at) }}</dd></div>
            </dl>
          </section>

          <section v-if="store.selected.error_code || store.selected.error_message" class="approval-section">
            <span class="eyebrow">LAST ERROR</span><h2 class="error-text">{{ store.selected.error_code || '执行错误' }}</h2>
            <p class="execution-error-message">{{ store.selected.error_message || '服务端未提供错误说明' }}</p>
          </section>
        </div>

        <aside class="approval-controls execution-reconcile">
          <div class="approval-actor">
            <span>当前登录账号</span>
            <strong>{{ auth.principal?.username }} · {{ auth.roleLabel }}</strong>
            <small>UI 仅提供权限提示，服务端仍会执行 admin 与 CSRF 校验。</small>
          </div>
          <strong>人工对账</strong>
          <p>仅在结果未知或服务端明确要求对账时提交。对账不会直接重复添加种子。</p>
          <label>原因（可选）<textarea v-model="reason" maxlength="1000" :disabled="!canAdmin || store.working" /></label>
          <button
            class="button primary"
            :disabled="store.working || !canAdmin || !canRequestReconciliation"
            @click="reconcile"
          >
            {{ store.working ? '提交中…' : '请求对账' }}
          </button>
          <small v-if="!canAdmin">需要管理员权限</small>
          <small v-else-if="store.selected.status === 'RECONCILIATION_PENDING'">对账请求已在等待处理</small>
          <small v-else-if="!store.selected.requires_reconciliation">当前状态无需对账</small>
        </aside>
      </div>
    </template>
  </section>
</template>
