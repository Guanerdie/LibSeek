import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '../stores/auth'
import type { AuthRole } from '../types'
import { safeInternalRedirect } from '../utils/navigation'
import ConfigurationView from '../views/ConfigurationView.vue'
import AutomationView from '../views/AutomationView.vue'
import LoginView from '../views/LoginView.vue'
import LibraryView from '../views/LibraryView.vue'
import ResourcesView from '../views/ResourcesView.vue'
import DownloadsView from '../views/DownloadsView.vue'

declare module 'vue-router' {
  interface RouteMeta {
    public?: boolean
    requiredRole?: AuthRole
  }
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: LoginView, meta: { public: true } },
    { path: '/', redirect: '/library' },
    { path: '/library', component: LibraryView },
    { path: '/library/:id/resources', component: ResourcesView },
    { path: '/downloads', component: DownloadsView },
    { path: '/automation', component: AutomationView, meta: { requiredRole: 'operator' } },
    { path: '/configuration', component: ConfigurationView, meta: { requiredRole: 'admin' } },
    { path: '/:pathMatch(.*)*', redirect: '/library' },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.initialize()

  if (to.meta.public) {
    if (to.path === '/login' && auth.principal) {
      return safeInternalRedirect(to.query.redirect) ?? '/library'
    }
    return true
  }

  if (!auth.principal) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  if (to.meta.requiredRole && !auth.hasRole(to.meta.requiredRole)) {
    return '/library'
  }

  return true
})

export default router
