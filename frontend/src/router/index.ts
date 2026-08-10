import { createRouter, createWebHistory } from 'vue-router'

import AdaptersView from '../views/AdaptersView.vue'
import ApprovalListView from '../views/ApprovalListView.vue'
import ApprovalView from '../views/ApprovalView.vue'
import DiscoveryView from '../views/DiscoveryView.vue'
import MediaView from '../views/MediaView.vue'
import IdentityView from '../views/IdentityView.vue'
import SystemView from '../views/SystemView.vue'
import TorrentCandidatesView from '../views/TorrentCandidatesView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
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
    { path: '/discovery', component: DiscoveryView },
    { path: '/adapters', component: AdaptersView },
  ],
})
