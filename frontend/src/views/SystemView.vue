<script setup lang="ts">
import { onMounted } from 'vue'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import { useSystemStore } from '../stores/system'
import { formatShanghai } from '../utils/format'

const store = useSystemStore()
onMounted(() => store.refresh())
</script>

<template>
  <section class="page">
    <PageHeader
      eyebrow="SYSTEM OVERVIEW"
      title="系统状态"
      description="检查本地控制面、数据库与只读发现链路的实时状态。"
    >
      <button class="button secondary" :disabled="store.loading" @click="store.refresh">刷新状态</button>
    </PageHeader>

    <PageState :loading="store.loading" :error="store.error" />
    <div v-if="store.data" class="status-grid">
      <article v-for="(component, name) in { API: store.data.api, Worker: store.data.worker, PostgreSQL: store.data.postgres }" :key="name" class="status-card">
        <div class="card-heading">
          <span class="card-icon">{{ name === 'API' ? '⌁' : name === 'Worker' ? '↻' : '▤' }}</span>
          <span class="health-dot" :class="component.healthy ? 'healthy' : 'unhealthy'"></span>
        </div>
        <span class="card-label">{{ name }}</span>
        <strong>{{ component.healthy ? '运行正常' : '需要检查' }}</strong>
        <p>{{ component.message }}</p>
        <small>检查于 {{ formatShanghai(component.checked_at) }}</small>
      </article>
    </div>

    <div v-if="store.data" class="panel config-panel">
      <div class="panel-title"><span class="eyebrow">INTEGRATION READINESS</span><h2>集成配置</h2></div>
      <div class="config-row">
        <div><strong>NextFind</strong><p>只读媒体源</p></div>
        <span :class="['config-state', store.data.nextfind_configured ? 'configured' : 'pending']">
          {{ store.data.nextfind_configured ? '已配置' : '未配置' }}
        </span>
      </div>
      <div class="config-row">
        <div><strong>TMDB</strong><p>真实只读元数据</p></div>
        <span :class="['config-state', store.data.tmdb_live_enabled && store.data.tmdb_configured ? 'configured' : 'disabled']">
          {{ store.data.tmdb_live_enabled && store.data.tmdb_configured ? '只读已启用' : '默认关闭' }}
        </span>
      </div>
      <div class="config-row">
        <div><strong>AvistaZ</strong><p>PT 搜索适配器</p></div>
        <span :class="['config-state', store.data.avistaz_live_enabled && store.data.avistaz_configured ? 'configured' : 'disabled']">{{ store.data.avistaz_status }}</span>
      </div>
      <div class="config-row">
        <div><strong>qBittorrent</strong><p>只读状态与下载前预检</p></div>
        <span :class="['config-state', store.data.qb_read_only_enabled && store.data.qb_configured ? 'configured' : 'disabled']">{{ store.data.qb_status }}</span>
      </div>
    </div>
  </section>
</template>
