<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { useAuthStore } from './stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const navItems = [
  { to: '/', label: '系统状态', icon: '◉' },
  { to: '/media', label: '未入库影视', icon: '▦' },
  { to: '/discovery', label: '发现任务', icon: '↻' },
  { to: '/adapters', label: '适配器', icon: '◇' },
  { to: '/approvals', label: '审批计划', icon: '✓' },
  { to: '/qbittorrent', label: 'qB 状态', icon: '⇄' },
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
      <nav aria-label="主导航">
        <RouterLink v-for="item in navItems" :key="item.to" :to="item.to">
          <span class="nav-icon">{{ item.icon }}</span>{{ item.label }}
        </RouterLink>
      </nav>
      <div class="phase-note">
        <span class="status-dot"></span>
        <div><strong>第三阶段</strong><small>审批与计划模式</small></div>
      </div>
    </aside>
    <main class="main-content">
      <header class="topbar">
        <div><span class="eyebrow">CONTROL PLANE</span><span class="divider">/</span> 本地管理端</div>
        <div class="topbar-actions">
          <span class="safe-badge">无下载写操作</span>
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
