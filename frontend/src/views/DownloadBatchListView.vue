<script setup lang="ts">
import { onMounted } from 'vue'
import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import { useDownloadBatchStore } from '../stores/downloadBatches'
import { formatShanghai } from '../utils/format'

const store = useDownloadBatchStore()
onMounted(() => store.load())
</script>

<template>
  <section class="page">
    <PageHeader eyebrow="BATCH QUEUE" title="下载批次" description="批量搜索并按当前安全自动化策略提交到 qBittorrent。">
      <div class="header-actions"><RouterLink class="button primary" to="/media">创建批次</RouterLink></div>
    </PageHeader>
    <PageState :loading="store.loading" :error="store.error" :empty="!store.loading && !store.error && !store.batches.length" empty-text="还没有下载批次" />
    <div v-if="store.batches.length" class="table-panel">
      <table>
        <thead><tr><th>批次</th><th>模式</th><th>站点</th><th>进度</th><th>状态</th><th>创建时间</th></tr></thead>
        <tbody>
          <tr v-for="batch in store.batches" :key="batch.id">
            <td><RouterLink :to="`/download-batches/${batch.id}`"><strong>{{ batch.name }}</strong></RouterLink></td>
            <td>{{ batch.mode === 'AUTO_SAFE' ? '安全自动' : '仅搜索' }}</td><td>{{ batch.site_id }}</td>
            <td>{{ batch.counts.COMPLETED || 0 }} / {{ batch.max_items }}</td><td>{{ batch.status }}</td>
            <td>{{ formatShanghai(batch.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
