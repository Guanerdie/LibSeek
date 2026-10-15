<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { useAuthStore } from './stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const primaryNavItems = [
  { to: '/library', label: '缺失', icon: '▦' },
  { to: '/downloads', label: '下载', icon: '↓' },
  { to: '/cleanup', label: '清理', icon: '✕' },
  { to: '/automation', label: '自动化', icon: '⟳' },
  { to: '/monitoring', label: '监控', icon: '◔' },
  { to: '/activity', label: '记录', icon: '☰' },
  { to: '/configuration', label: '设置', icon: '⚙' },
]

const isPublicRoute = computed(() => Boolean(route.meta.public))
const activeNavItem = computed(() =>
  primaryNavItems.find((item) => route.path === item.to || route.path.startsWith(`${item.to}/`)),
)

const serviceStatus = ref<'checking' | 'ok' | 'error'>('checking')
const serviceStatusLabel = computed(() =>
  ({ checking: '检查服务中', ok: '服务正常', error: '服务异常' })[serviceStatus.value],
)
let healthTimer: ReturnType<typeof setInterval> | undefined
let healthRequest: AbortController | undefined

async function checkHealth(): Promise<void> {
  if (isPublicRoute.value || healthRequest) return
  const controller = new AbortController()
  healthRequest = controller
  const timeout = setTimeout(() => controller.abort(), 5000)
  try {
    const response = await fetch('/api/health', { signal: controller.signal, cache: 'no-store' })
    const result = response.ok ? await response.json() : null
    serviceStatus.value = result?.status === 'ok' ? 'ok' : 'error'
  } catch {
    serviceStatus.value = 'error'
  } finally {
    clearTimeout(timeout)
    healthRequest = undefined
  }
}

onMounted(() => {
  void checkHealth()
  healthTimer = setInterval(() => void checkHealth(), 30_000)
})
watch(isPublicRoute, (publicRoute) => {
  if (!publicRoute) void checkHealth()
})
onUnmounted(() => {
  clearInterval(healthTimer)
  healthRequest?.abort()
})

async function logout(): Promise<void> {
  if (await auth.logout()) await router.replace('/login')
}
</script>

<template>
  <RouterView v-if="isPublicRoute" />
  <div v-else class="app-shell">
    <main class="main-content">
      <header class="topbar">
        <RouterLink class="topbar-brand" to="/library" aria-label="UNIN 工作台">
          <span class="brand-mark">U</span>
          <span><strong>UNIN</strong><small>影视资源编排</small></span>
        </RouterLink>
        <nav class="topbar-nav" aria-label="主导航">
          <RouterLink
            v-for="item in primaryNavItems"
            :key="item.to"
            :to="item.to"
            :aria-label="item.label"
          >
            {{ item.to === '/automation' ? '自动化策略' : item.label }}
          </RouterLink>
        </nav>
        <div class="topbar-context">
          <span class="eyebrow">UNIN · 影视资源</span>
          <span class="divider">/</span>
          <strong>{{ activeNavItem?.label ?? '工作台' }}</strong>
        </div>
        <div class="topbar-tools">
          <RouterLink class="topbar-search" to="/library" aria-label="打开缺失影视搜索">
            <span aria-hidden="true">⌕</span>
            <span>搜索缺失影视</span>
            <kbd>Ctrl K</kbd>
          </RouterLink>
          <span class="topbar-live" :data-status="serviceStatus" role="status">
            <i aria-hidden="true"></i> {{ serviceStatusLabel }}
          </span>
        </div>
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
