<script setup lang="ts">
import { ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useAuthStore } from '../stores/auth'
import { useMediaImportStore } from '../stores/mediaImports'
import type { MediaImportOperation, MediaImportStatus } from '../types'
import { formatShanghai, statusLabel } from '../utils/format'

const route = useRoute()
const auth = useAuthStore()
const store = useMediaImportStore()
const status = ref<MediaImportStatus | ''>('')
const downloadJobId = ref(String(route.query.download_job_id ?? ''))
const statusOptions: MediaImportStatus[] = [
  'PREFLIGHT_REQUIRED',
  'REVIEW_REQUIRED',
  'APPROVED_PLAN_ONLY',
  'REJECTED',
  'REVOKED',
]

watch(
  () => route.query.download_job_id,
  (value) => {
    downloadJobId.value = queryValue(value)
    load(1)
  },
  { immediate: true },
)

function queryValue(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] ?? '')
  return String(value ?? '')
}

function load(page = 1): void {
  void store.loadList(
    {
      status: status.value || undefined,
      downloadJobId: downloadJobId.value.trim() || undefined,
    },
    page,
  )
}

function operationLabel(value: MediaImportOperation): string {
  return value === 'HARDLINK' ? '硬链接' : '复制'
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
</script>

<template>
  <section class="page wide-page media-import-page">
    <PageHeader
      eyebrow="MEDIA IMPORT PLANS"
      title="入库规划"
      description="审核下载任务的不可变文件清单、目标映射和只读预检记录。"
    >
      <div class="header-actions">
        <button class="button secondary" :disabled="store.listLoading" @click="load(store.page)">刷新列表</button>
        <RouterLink v-if="auth.hasRole('operator')" class="button primary" :to="{ path: '/media-imports/new', query: downloadJobId ? { download_job_id: downloadJobId } : {} }">创建规划</RouterLink>
      </div>
    </PageHeader>

    <div class="phase-banner media-import-safety"><span>仅规划</span>仅规划，不操作媒体文件</div>

    <form class="filter-bar media-import-filters" @submit.prevent="load(1)">
      <select v-model="status" aria-label="入库规划状态">
        <option value="">全部状态</option>
        <option v-for="item in statusOptions" :key="item" :value="item">{{ statusLabel(item) }}</option>
      </select>
      <input v-model="downloadJobId" aria-label="下载任务 ID 筛选" placeholder="下载任务 ID" />
      <button class="button" type="submit">筛选</button>
      <button type="button" class="button secondary" @click="status = ''; downloadJobId = ''; load(1)">清除</button>
    </form>

    <PageState
      :loading="store.listLoading"
      :error="store.listError"
      :empty="!store.listLoading && !store.listError && store.requests.length === 0 && store.total === 0"
      empty-text="暂无入库规划"
    />

    <div v-if="!store.listLoading && !store.listError && store.requests.length" class="candidate-table-wrap media-import-desktop-list">
      <table class="media-import-table">
        <thead><tr><th>媒体 / 下载任务</th><th>规划状态</th><th>方式 / 文件</th><th>源 → 目标</th><th>只读预检</th><th>申请时间</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="item in store.requests" :key="item.id">
            <td><strong>{{ item.media_title }}</strong><small class="mono">{{ item.download_job_id }}</small></td>
            <td><StatusPill :status="item.status" /><small class="mono">{{ item.id }}</small></td>
            <td><strong>{{ operationLabel(item.proposed_operation) }}</strong><small>{{ item.file_count }} 个文件 · {{ formatBytes(item.size_bytes) }}</small></td>
            <td><strong class="mono">{{ item.source_root_ref }}</strong><small class="mono">→ {{ item.target_root_ref }}</small></td>
            <td><StatusPill v-if="item.preflight_status" :status="item.preflight_status" /><strong v-else class="pending-label">待内部预检</strong><small>{{ item.preflight_checked_at ? formatShanghai(item.preflight_checked_at) : '尚无受信只读检查记录' }}</small></td>
            <td><strong>{{ formatShanghai(item.requested_at) }}</strong><small>{{ item.requested_by }}</small></td>
            <td><RouterLink class="table-action" :to="`/media-imports/${item.id}`">查看规划</RouterLink></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="!store.listLoading && !store.listError && store.requests.length" class="media-import-mobile-list">
      <article v-for="item in store.requests" :key="item.id" class="media-import-mobile-item">
        <header><div><strong>{{ item.media_title }}</strong><small>{{ operationLabel(item.proposed_operation) }} · {{ item.file_count }} 个文件</small></div><StatusPill :status="item.status" /></header>
        <dl>
          <div><dt>下载任务</dt><dd class="mono">{{ item.download_job_id }}</dd></div>
          <div><dt>映射</dt><dd class="mono">{{ item.source_root_ref }} → {{ item.target_root_ref }}</dd></div>
          <div><dt>预检</dt><dd><StatusPill v-if="item.preflight_status" :status="item.preflight_status" /><span v-else>待内部预检</span></dd></div>
          <div><dt>申请</dt><dd>{{ formatShanghai(item.requested_at) }}</dd></div>
        </dl>
        <RouterLink class="button secondary" :to="`/media-imports/${item.id}`">查看规划</RouterLink>
      </article>
    </div>

    <div v-if="!store.listLoading && !store.listError && (store.total > 0 || store.page > 1)" class="pagination standalone-pagination">
      <span>共 {{ store.total }} 条 · 第 {{ store.page }} 页</span>
      <div>
        <button class="button small" :disabled="store.page <= 1" @click="load(store.page - 1)">上一页</button>
        <button class="button small" :disabled="store.page * store.pageSize >= store.total" @click="load(store.page + 1)">下一页</button>
      </div>
    </div>
  </section>
</template>
