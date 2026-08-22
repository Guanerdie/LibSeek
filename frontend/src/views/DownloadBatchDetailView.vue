<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import { useAuthStore } from '../stores/auth'
import { useDownloadBatchStore } from '../stores/downloadBatches'

const route = useRoute(); const auth = useAuthStore(); const store = useDownloadBatchStore()
const id = String(route.params.id); let timer: ReturnType<typeof globalThis.setInterval> | undefined
onMounted(async () => { await store.loadOne(id); timer = globalThis.setInterval(() => store.loadOne(id), 4000) })
onBeforeUnmount(() => globalThis.clearInterval(timer))
const retryable = (status: string) => status === 'FAILED' || status === 'MANUAL_REQUIRED'
</script>

<template>
  <section class="page">
    <PageHeader eyebrow="BATCH DETAIL" :title="store.current?.name || '下载批次'" description="自动刷新批次状态；异常条目不会阻塞其他任务。" />
    <PageState :loading="store.loading && !store.current" :error="store.error" :empty="false" />
    <template v-if="store.current">
      <div class="notice-state">{{ store.current.mode === 'AUTO_SAFE' ? '安全自动模式' : '仅搜索模式' }} · {{ store.current.site_id }} · {{ store.current.status }} · {{ store.current.launch_mode === 'SCHEDULED_START' ? '由 qB 队列调度启动' : '添加后暂停' }}</div>
      <div class="table-panel">
        <table>
          <thead><tr><th>影视</th><th>类型</th><th>状态</th><th>问题</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="item in store.current.items" :key="item.id">
              <td><strong>{{ item.title }}</strong><small>{{ item.year || '—' }}</small></td>
              <td>{{ item.media_type === 'movie' ? '电影' : '电视剧' }}</td><td>{{ item.status }}</td>
              <td>{{ item.error_message || '—' }}</td><td>
                <div class="row-actions">
                  <RouterLink :to="`/media/${item.media_item_id}/torrents`">查看</RouterLink>
                  <button v-if="retryable(item.status) && auth.hasRole('operator')" class="button small" :disabled="store.working" @click="store.retry(store.current!.id, item.id)">重试</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>
