<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { useAuthStore } from './stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const primaryNavItems = [
  { to: '/media', label: '待处理', icon: '▦' },
  { to: '/download-jobs', label: '下载', icon: '↓' },
  { to: '/download-batches', label: '批次', icon: '≡' },
  { to: '/configuration', label: '设置', icon: '⚙' },
]

const isPublicRoute = computed(() => Boolean(route.meta.public))

async function logout(): Promise<void> {
  if (await auth.logout()) await router.replace('/login')
}
</script>

<template>
  <RouterView v-if="isPublicRoute" />
  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">U</span>
        <div>
          <strong>UNIN</strong>
          <small>MEDIA ORCHESTRATOR</small>
        </div>
      </div>
      <nav class="app-nav" aria-label="主导航">
        <RouterLink
          v-for="item in primaryNavItems"
          :key="item.to"
          class="primary-nav-link"
          :to="item.to"
          :aria-label="item.label"
          :title="item.label"
        >
          <span class="nav-icon">{{ item.icon }}</span><span class="nav-label">{{ item.label }}</span>
        </RouterLink>
      </nav>
      <div class="phase-note">
        <span class="status-dot"></span>
        <div><strong>人工确认</strong><small>默认工作模式</small></div>
      </div>
    </aside>
    <main class="main-content">
      <header class="topbar">
        <div><span class="eyebrow">CONTROL PLANE</span><span class="divider">/</span> 本地管理端</div>
        <div class="topbar-actions">
          <span class="safe-badge">高风险写操作需显式确认</span>
          <div v-if="auth.principal" class="session-summary">
            <span><strong>{{ auth.principal.username }}</strong><small>{{ auth.roleLabel }}</small></span>
            <button class="button secondary small" :disabled="auth.working" @click="logout">
              {{ auth.working ? '退出中…' : '退出' }}
            </button>
          </div>
        </div>
      </header>
      <div v-if="auth.error" class="session-error" role="alert">{{ auth.error }}</div>
      <RouterView />
    </main>
  </div>
</template>
