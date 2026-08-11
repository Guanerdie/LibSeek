import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '../stores/auth'
import type { AuthRole } from '../types'
import { safeInternalRedirect } from '../utils/navigation'
import AdaptersView from '../views/AdaptersView.vue'
import AutomationView from '../views/AutomationView.vue'
import ApprovalListView from '../views/ApprovalListView.vue'
import ApprovalView from '../views/ApprovalView.vue'
import DiscoveryView from '../views/DiscoveryView.vue'
import DownloadJobDetailView from '../views/DownloadJobDetailView.vue'
import DownloadJobListView from '../views/DownloadJobListView.vue'
import ExecutionDetailView from '../views/ExecutionDetailView.vue'
import ExecutionListView from '../views/ExecutionListView.vue'
import MediaView from '../views/MediaView.vue'
import IdentityView from '../views/IdentityView.vue'
import LoginView from '../views/LoginView.vue'
import MediaImportCreateView from '../views/MediaImportCreateView.vue'
import MediaImportDetailView from '../views/MediaImportDetailView.vue'
import MediaImportListView from '../views/MediaImportListView.vue'
import QbittorrentView from '../views/QbittorrentView.vue'
import SystemView from '../views/SystemView.vue'
import TorrentCandidatesView from '../views/TorrentCandidatesView.vue'

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
    { path: '/', component: SystemView },
    { path: '/media', component: MediaView },
    { path: '/media/:id/identity', component: IdentityView },
    { path: '/media/:id/torrents', component: TorrentCandidatesView },
    {
      path: '/media/:mediaId/torrent-searches/:searchId/candidates/:candidateId/approval',
      component: ApprovalView,
    },
    { path: '/approvals', component: ApprovalListView },
    { path: '/approvals/:id', component: ApprovalView },
    { path: '/executions', component: ExecutionListView },
    { path: '/executions/:id', component: ExecutionDetailView },
    { path: '/download-jobs', component: DownloadJobListView },
    { path: '/download-jobs/:id', component: DownloadJobDetailView },
    { path: '/media-imports', component: MediaImportListView },
    {
      path: '/media-imports/new',
      component: MediaImportCreateView,
      meta: { requiredRole: 'operator' },
    },
    { path: '/media-imports/:id', component: MediaImportDetailView },
    { path: '/discovery', component: DiscoveryView },
    { path: '/adapters', component: AdaptersView },
    { path: '/automation', component: AutomationView },
    { path: '/qbittorrent', component: QbittorrentView },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.initialize()

  if (to.meta.public) {
    if (to.path === '/login' && auth.principal) {
      return safeInternalRedirect(to.query.redirect) ?? '/'
    }
    return true
  }

  if (!auth.principal) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  if (to.meta.requiredRole && !auth.hasRole(to.meta.requiredRole)) {
    return '/'
  }

  return true
})

export default router
