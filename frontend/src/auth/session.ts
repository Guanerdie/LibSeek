import { setUnauthorizedHandler } from '../api/client'
import type { useAuthStore } from '../stores/auth'
import type { Router } from 'vue-router'

type AuthStore = ReturnType<typeof useAuthStore>

export function redirectExpiredSession(auth: AuthStore, router: Router): void {
  const hadInitializedSession = auth.initialized
  auth.expireSession()
  if (!hadInitializedSession || router.currentRoute.value.path === '/login') return
  const current = router.currentRoute.value
  const query = current.meta.public ? undefined : { redirect: current.fullPath }
  void router.replace({ path: '/login', query })
}

export function installSessionExpiryHandler(auth: AuthStore, router: Router): void {
  setUnauthorizedHandler(() => redirectExpiredSession(auth, router))
}
