<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'
import { safeInternalRedirect } from '../utils/navigation'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const username = ref('')
const password = ref('')

async function submit(): Promise<void> {
  const normalizedUsername = username.value.trim()
  if (!normalizedUsername || !password.value) return
  let authenticated = false
  try {
    authenticated = await auth.login(normalizedUsername, password.value)
  } finally {
    password.value = ''
  }
  if (authenticated) {
    await router.replace(safeInternalRedirect(route.query.redirect) ?? '/')
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-card" aria-labelledby="login-title">
      <div class="login-brand"><span class="brand-mark">U</span><div><strong>UNIN</strong><small>MEDIA ORCHESTRATOR</small></div></div>
      <span class="eyebrow">LOCAL CONTROL PLANE</span>
      <h1 id="login-title">登录本地管理端</h1>
      <p>使用部署时配置的本地账号。凭据不会写入浏览器存储。</p>
      <form @submit.prevent="submit">
        <label>
          用户名
          <input v-model="username" name="username" autocomplete="username" maxlength="120" required />
        </label>
        <label>
          密码
          <input v-model="password" name="password" type="password" autocomplete="current-password" required />
        </label>
        <div v-if="auth.error" class="login-error" role="alert">{{ auth.error }}</div>
        <button class="button primary" type="submit" :disabled="auth.working || !username.trim() || !password">
          {{ auth.working ? '正在登录…' : '登录' }}
        </button>
      </form>
    </section>
  </main>
</template>
