<script setup lang="ts">
defineProps<{ loading?: boolean; error?: string | null; empty?: boolean; emptyText?: string }>()
</script>

<template>
  <div v-if="loading" class="page-state loading-state"><span class="spinner"></span>正在读取数据…</div>
  <div v-else-if="error" class="page-state error-state">{{ error }}</div>
  <div v-else-if="empty" class="page-state empty-state">{{ emptyText ?? '暂无数据' }}</div>
</template>
