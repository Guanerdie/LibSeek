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
      description="固定候选快照、人工决策、预检和不可执行下载计划。"
    />
    <div class="phase-banner"><span>计划模式</span>审批只生成不可执行计划，不获取 .torrent、不写入 qBittorrent；后续由管理员一次确认下载执行，或显式启用并满足自动执行策略，且服务端执行总闸开启时，才可能写入。</div>
    <PageState
      :loading="store.loading"
      :error="store.error"
      :empty="!store.loading && !store.error && store.approvals.length === 0"
      empty-text="当前没有审批请求"
    />
    <div v-if="store.approvals.length" class="table-panel approval-desktop-list">
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
    <div v-if="store.approvals.length" class="approval-mobile-list">
      <article v-for="item in store.approvals" :key="item.id" class="approval-mobile-item">
        <header>
          <div>
            <strong>{{ item.candidate.media_title }}</strong>
            <small>{{ item.candidate.release_title }}</small>
          </div>
          <StatusPill :status="item.status" />
        </header>
        <dl>
          <div><dt>申请人</dt><dd>{{ item.requested_by }}</dd></div>
          <div><dt>预检</dt><dd>{{ item.preflight_result?.overall_status ?? '未执行' }}</dd></div>
          <div><dt>有效期</dt><dd>{{ formatShanghai(item.expires_at) }}</dd></div>
        </dl>
        <a class="button secondary" :href="`/approvals/${item.id}`">查看审批</a>
      </article>
    </div>
  </section>
</template>
