<script setup lang="ts">
import { onMounted } from 'vue'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import { useAdaptersStore } from '../stores/adapters'

const store = useAdaptersStore()
onMounted(() => store.load())

function capabilityLabel(value: string): string {
  return value.replaceAll('_', ' ')
}
</script>

<template>
  <section class="page">
    <PageHeader
      eyebrow="ADAPTER REGISTRY"
      title="适配器"
      description="统一能力声明与当前阶段启用状态；此页面从不显示凭据。"
    >
      <button class="button secondary" :disabled="store.loading" @click="store.load">刷新列表</button>
    </PageHeader>
    <PageState
      :loading="store.loading"
      :error="store.error"
      :empty="!store.loading && !store.error && store.items.length === 0"
    />
    <div v-if="store.items.length" class="adapter-grid">
      <article v-for="adapter in store.items" :key="adapter.id" class="panel adapter-card">
        <header><div><span class="adapter-type">{{ adapter.adapter_type }}</span><h2>{{ adapter.name }}</h2></div><span :class="['config-state', adapter.enabled ? 'configured' : 'disabled']">{{ adapter.enabled ? '已启用' : '未启用' }}</span></header>
        <p>{{ adapter.description }}</p>
        <div class="adapter-meta"><span>v{{ adapter.version }}</span><span>{{ adapter.mode }}</span></div>
        <div class="capabilities">
          <span v-for="(enabled, name) in adapter.capabilities" :key="name" :class="{ off: !enabled }"><b>{{ enabled ? '✓' : '×' }}</b>{{ capabilityLabel(String(name)) }}</span>
        </div>
      </article>
    </div>
  </section>
</template>

