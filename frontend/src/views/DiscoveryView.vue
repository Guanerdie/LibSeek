<script setup lang="ts">
import { onMounted } from 'vue'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { useDiscoveryStore } from '../stores/discovery'
import { formatShanghai } from '../utils/format'

const store = useDiscoveryStore()
onMounted(() => store.load())
</script>

<template>
  <section class="page">
    <PageHeader
      eyebrow="DISCOVERY QUEUE"
      title="发现任务"
      description="每次任务只读取外部数据；相同活跃任务会自动合并。"
    >
      <button class="button primary" :disabled="store.creating" @click="store.create">
        {{ store.creating ? '正在创建…' : '新建只读发现' }}
      </button>
    </PageHeader>

    <PageState
      :loading="store.loading"
      :error="store.error"
      :empty="!store.loading && !store.error && store.runs.length === 0"
      empty-text="尚无发现任务"
    />
    <div v-if="store.runs.length" class="split-layout">
      <div class="task-list panel">
        <button v-for="run in store.runs" :key="run.id" :class="['task-row', { active: store.selected?.id === run.id }]" @click="store.selectRun(run.id)">
          <span><strong>{{ run.source }}</strong><small>{{ formatShanghai(run.created_at) }}</small></span>
          <StatusPill :status="run.status" />
        </button>
      </div>
      <div class="panel task-detail">
        <div v-if="!store.selected" class="page-state empty-state">选择任务查看详情与审计时间线</div>
        <template v-else>
          <div class="detail-heading"><div><span class="eyebrow">RUN DETAIL</span><h2>{{ store.selected.id }}</h2></div><StatusPill :status="store.selected.status" /></div>
          <div class="metric-row">
            <div><span>发现</span><strong>{{ store.selected.discovered_count }}</strong></div>
            <div><span>新增</span><strong>{{ store.selected.created_count }}</strong></div>
            <div><span>更新</span><strong>{{ store.selected.updated_count }}</strong></div>
          </div>
          <dl class="detail-list">
            <div><dt>开始时间</dt><dd>{{ formatShanghai(store.selected.started_at) }}</dd></div>
            <div><dt>结束时间</dt><dd>{{ formatShanghai(store.selected.finished_at) }}</dd></div>
            <div v-if="store.selected.error_code"><dt>错误</dt><dd class="error-text">{{ store.selected.error_code }} · {{ store.selected.error_message }}</dd></div>
          </dl>
          <div class="timeline">
            <h3>审计时间线</h3>
            <div v-if="!store.selected.audit_events?.length" class="empty-inline">暂无审计事件</div>
            <article v-for="event in store.selected.audit_events" :key="event.id">
              <span class="timeline-dot"></span><div><strong>{{ event.event_type }}</strong><small>{{ formatShanghai(event.created_at) }}</small><pre>{{ JSON.stringify(event.sanitized_details, null, 2) }}</pre></div>
            </article>
          </div>
        </template>
      </div>
    </div>
  </section>
</template>

