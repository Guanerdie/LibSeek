<script setup lang="ts">
import { onMounted } from 'vue'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useApprovalStore } from '../stores/approvals'
import { formatShanghai } from '../utils/format'

const store = useApprovalStore()
onMounted(() => store.loadList())
</script>

<template>
  <section class="page">
    <PageHeader
      eyebrow="IMMUTABLE APPROVALS"
      title="审批与下载计划"
      description="固定候选快照、人工决策、预检和只读下载计划。"
    />
    <div class="phase-banner"><span>计划模式</span>审批不会获取 .torrent，也不会向 qBittorrent 添加任务。</div>
    <PageState
      :loading="store.loading"
      :error="store.error"
      :empty="!store.loading && !store.error && store.approvals.length === 0"
      empty-text="当前没有审批请求"
    />
    <div v-if="store.approvals.length" class="table-panel">
      <table>
        <thead><tr><th>影视 / 发布名</th><th>状态</th><th>申请人</th><th>预检</th><th>有效期</th><th>计划</th></tr></thead>
        <tbody>
          <tr v-for="item in store.approvals" :key="item.id">
            <td><strong>{{ item.candidate.media_title }}</strong><small>{{ item.candidate.release_title }}</small></td>
            <td><StatusPill :status="item.status" /></td>
            <td>{{ item.requested_by }}</td>
            <td>{{ item.preflight_result?.overall_status ?? '未执行' }}</td>
            <td>{{ formatShanghai(item.expires_at) }}</td>
            <td><div class="row-actions"><a :href="`/approvals/${item.id}`">查看审批</a></div></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
