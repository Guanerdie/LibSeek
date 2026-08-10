import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiError, authApi, setApiCsrfToken } from '../api/client'
import type { AuthRole, Principal } from '../types'

const ROLE_RANK: Record<AuthRole, number> = {
  viewer: 10,
  operator: 20,
  admin: 30,
}

const ROLE_LABEL: Record<AuthRole, string> = {
  viewer: '查看者',
  operator: '操作者',
  admin: '管理员',
}

export const useAuthStore = defineStore('auth', () => {
  const principal = ref<Principal | null>(null)
  const csrfToken = ref<string | null>(null)
  const initialized = ref(false)
  const loading = ref(false)
  const working = ref(false)
  const error = ref<string | null>(null)
  const roleLabel = computed(() => principal.value ? ROLE_LABEL[principal.value.role] : '')
  let initialization: Promise<void> | null = null

  function replaceCsrfToken(token: string | null): void {
    csrfToken.value = token
    setApiCsrfToken(token)
  }

  function expireSession(): void {
    principal.value = null
    replaceCsrfToken(null)
    error.value = null
    initialized.value = true
  }

  function hasRole(required: AuthRole): boolean {
    return principal.value !== null && ROLE_RANK[principal.value.role] >= ROLE_RANK[required]
  }

  async function bootstrap(): Promise<void> {
    loading.value = true
    error.value = null
    principal.value = null
    try {
      const csrf = await authApi.csrf()
      replaceCsrfToken(csrf.csrf_token)
      try {
        principal.value = await authApi.me()
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 401) {
          principal.value = null
          // The global 401 handler may clear memory while checking an anonymous session.
          // Keep the freshly issued bootstrap token so the login POST remains possible.
          replaceCsrfToken(csrf.csrf_token)
        } else {
          throw caught
        }
      }
    } catch (caught) {
      principal.value = null
      error.value = caught instanceof Error ? caught.message : '无法检查登录状态'
    } finally {
      initialized.value = true
      loading.value = false
    }
  }

  async function initialize(): Promise<void> {
    if (initialized.value) return
    if (!initialization) {
      initialization = bootstrap().finally(() => {
        initialization = null
      })
    }
    await initialization
  }

  async function login(username: string, password: string): Promise<boolean> {
    working.value = true
    error.value = null
    try {
      // Refresh immediately before login so an idle bootstrap token cannot expire in the form.
      const csrf = await authApi.csrf()
      replaceCsrfToken(csrf.csrf_token)
      const response = await authApi.login(username, password)
      principal.value = { username: response.username, role: response.role }
      replaceCsrfToken(response.csrf_token)
      initialized.value = true
      return true
    } catch (caught) {
      principal.value = null
      error.value = caught instanceof Error ? caught.message : '登录失败'
      return false
    } finally {
      working.value = false
    }
  }

  async function logout(): Promise<boolean> {
    working.value = true
    error.value = null
    try {
      await authApi.logout()
      expireSession()
      return true
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        expireSession()
        return true
      }
      error.value = caught instanceof Error ? caught.message : '退出登录失败'
      return false
    } finally {
      working.value = false
    }
  }

  return {
    principal,
    roleLabel,
    csrfToken,
    initialized,
    loading,
    working,
    error,
    initialize,
    login,
    logout,
    expireSession,
    hasRole,
  }
})
