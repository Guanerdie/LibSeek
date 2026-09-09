<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'
import { safeInternalRedirect } from '../utils/navigation'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const username = ref('')
const password = ref('')
const passwordConfirmation = ref('')
const remember = ref(false)
const validationError = ref<string | null>(null)
const setupMode = computed(() => auth.adminInitialized === false)
const formReady = computed(() => auth.adminInitialized !== null)
const canSubmit = computed(() => {
  if (!formReady.value || auth.working || !username.value.trim() || !password.value) return false
  return !setupMode.value || (password.value.length >= 6 && Boolean(passwordConfirmation.value))
})

async function submit(): Promise<void> {
  const normalizedUsername = username.value.trim()
  if (!normalizedUsername || !password.value) return
  validationError.value = null
  if (setupMode.value && password.value.length < 6) {
    validationError.value = '管理员密码至少需要 6 位'
    return
  }
  if (setupMode.value && password.value !== passwordConfirmation.value) {
    validationError.value = '两次输入的密码不一致'
    password.value = ''
    passwordConfirmation.value = ''
    return
  }
  let authenticated = false
  const creatingAdmin = setupMode.value
  try {
    authenticated = creatingAdmin
      ? await auth.setupAdmin(normalizedUsername, password.value)
      : await auth.login(normalizedUsername, password.value, remember.value)
  } finally {
    password.value = ''
    passwordConfirmation.value = ''
  }
  if (authenticated) {
    await router.replace(
      creatingAdmin ? '/configuration' : (safeInternalRedirect(route.query.redirect) ?? '/media'),
    )
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-card" aria-labelledby="login-title">
      <div class="login-brand"><span class="brand-mark">U</span><div><strong>UNIN</strong><small>MEDIA ORCHESTRATOR</small></div></div>
      <span class="eyebrow">LOCAL CONTROL PLANE</span>
      <h1 id="login-title">{{ setupMode ? '创建管理员' : '登录本地管理端' }}</h1>
      <p v-if="setupMode">首次使用只需创建一个本地管理员，密码至少 6 位，随后在管理页面完成连接配置。</p>
      <p v-else-if="formReady">使用本地管理账号登录。凭据不会写入浏览器存储。</p>
      <p v-else>正在检查本机初始化状态…</p>
      <form v-if="formReady" @submit.prevent="submit">
        <label>
          用户名
          <input
            v-model="username"
            name="username"
            :autocomplete="setupMode ? 'off' : 'username'"
            maxlength="120"
            required
          />
        </label>
        <label>
          密码
          <input
            v-model="password"
            name="password"
            type="password"
            :autocomplete="setupMode ? 'new-password' : 'current-password'"
            :minlength="setupMode ? 6 : undefined"
            maxlength="1024"
            required
          />
        </label>
        <label v-if="setupMode">
          确认密码
          <input
            v-model="passwordConfirmation"
            name="password_confirmation"
            type="password"
            autocomplete="new-password"
            minlength="6"
            maxlength="1024"
            required
          />
        </label>
        <label v-if="!setupMode" class="login-remember">
          <input v-model="remember" name="remember" type="checkbox" />
          记住这台设备（30 天内免登录）
        </label>
        <div v-if="validationError || auth.error" class="login-error" role="alert">
          {{ validationError || auth.error }}
        </div>
        <button class="button primary" type="submit" :disabled="!canSubmit">
          {{ auth.working ? (setupMode ? '正在创建…' : '正在登录…') : (setupMode ? '创建并继续' : '登录') }}
        </button>
      </form>
      <div v-else-if="auth.error" class="login-error" role="alert">{{ auth.error }}</div>
    </section>
  </main>
</template>
