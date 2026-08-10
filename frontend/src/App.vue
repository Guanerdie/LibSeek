<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'

const navItems = [
  { to: '/', label: '系统状态', icon: '◉' },
  { to: '/media', label: '未入库影视', icon: '▦' },
  { to: '/discovery', label: '发现任务', icon: '↻' },
  { to: '/adapters', label: '适配器', icon: '◇' },
  { to: '/approvals', label: '审批计划', icon: '✓' },
]
</script>

<template>
  <div class="app-shell">
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
        <span class="safe-badge">无下载写操作</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>
