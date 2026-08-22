<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { useAuthStore } from './stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const primaryNavItems = [
  { to: '/library', label: '缺失', icon: '▦' },
  { to: '/downloads', label: '下载', icon: '↓' },
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
        <div><strong>日常模式</strong><small>发现 · 选择 · 下载</small></div>
      </div>
    </aside>
    <main class="main-content">
      <header class="topbar">
        <div><span class="eyebrow">UNIN</span><span class="divider">/</span> 影视资源助手</div>
        <div class="topbar-actions">
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
