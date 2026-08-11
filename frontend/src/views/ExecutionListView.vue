<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useExecutionStore } from '../stores/executions'
import type { DownloadExecutionStatus } from '../types'
import { formatShanghai } from '../utils/format'

const store = useExecutionStore()
const status = ref<DownloadExecutionStatus | ''>('')
const statusOptions: DownloadExecutionStatus[] = [
  'PENDING',
  'RETRY_WAIT',
  'VALIDATING',
  'SUBMITTING',
  'SUBMITTED',
  'ALREADY_PRESENT',
  'OUTCOME_UNKNOWN',
  'RECONCILIATION_REQUIRED',
  'RECONCILIATION_PENDING',
  'FAILED',
  'CANCELLED',
]

onMounted(() => store.loadList())

function load(page = 1): void {
  void store.loadList(status.value || undefined, page)
}
</script>

<template>
  <section class="page wide-page">
    <PageHeader
      eyebrow="DOWNLOAD EXECUTIONS"
      title="下载执行"
      description="查看审批计划的执行状态、重试与对账要求。"
    >
      <button class="button secondary" :disabled="store.loading" @click="load(store.page)">刷新列表</button>
    </PageHeader>

    <div class="phase-banner"><span>执行控制</span>创建执行记录不代表下载已经成功；以状态、校验结果和后续任务为准。</div>
    <div class="filter-bar execution-filter">
      <select v-model="status" aria-label="执行状态" @change="load(1)">
        <option value="">全部状态</option>
        <option v-for="item in statusOptions" :key="item" :value="item">{{ item }}</option>
      </select>
    </div>

    <PageState
      :loading="store.loading"
      :error="store.error"
      :empty="!store.loading && !store.error && store.executions.length === 0 && store.total === 0"
      empty-text="暂无下载执行记录"
    />
    <div v-if="!store.loading && !store.error && store.executions.length === 0 && store.total > 0" class="notice-state warning-state">当前页暂无记录，可返回上一页或刷新筛选结果。</div>

    <div v-if="!store.loading && store.executions.length" class="candidate-table-wrap">
      <table class="execution-table">
        <thead><tr><th>执行 / 审批</th><th>状态</th><th>启动模式</th><th>尝试</th><th>对账</th><th>请求人 / 时间</th><th>错误</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="execution in store.executions" :key="execution.id">
            <td class="execution-id"><strong class="mono">{{ execution.id }}</strong><small class="mono">{{ execution.approval_id }}</small></td>
            <td><StatusPill :status="execution.status" /></td>
            <td><strong>{{ execution.launch_mode === 'ADD_PAUSED' ? '添加后暂停' : '立即开始' }}</strong></td>
            <td><strong>{{ execution.attempts }} / {{ execution.max_attempts }}</strong><small>{{ execution.next_retry_at ? formatShanghai(execution.next_retry_at) : '无等待重试' }}</small></td>
            <td><strong :class="execution.requires_reconciliation ? 'risk-text' : ''">{{ execution.requires_reconciliation ? '需要人工对账' : '无需对账' }}</strong></td>
            <td><strong>{{ execution.requested_by }}</strong><small>{{ formatShanghai(execution.requested_at) }}</small></td>
            <td><strong :class="execution.error_code ? 'error-text' : ''">{{ execution.error_code || '—' }}</strong><small>{{ execution.error_message || '无' }}</small></td>
            <td><RouterLink class="table-action" :to="`/executions/${execution.id}`">查看详情</RouterLink></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="!store.loading && !store.error && (store.total > 0 || store.page > 1)" class="pagination standalone-pagination">
      <span>共 {{ store.total }} 条 · 第 {{ store.page }} 页</span>
      <div>
        <button class="button small" :disabled="store.page <= 1" @click="load(store.page - 1)">上一页</button>
        <button class="button small" :disabled="store.page * store.pageSize >= store.total" @click="load(store.page + 1)">下一页</button>
      </div>
    </div>
  </section>
</template>
