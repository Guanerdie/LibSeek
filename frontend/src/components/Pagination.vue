<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    page: number
    total: number
    pageSize: number
    label?: string
  }>(),
  { label: '分页' },
)

const emit = defineEmits<{
  change: [page: number]
}>()

const pageCount = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
const visiblePages = computed(() => {
  const count = pageCount.value
  const current = Math.min(Math.max(props.page, 1), count)
  const start = Math.max(1, Math.min(current - 2, count - 4))
  const end = Math.min(count, start + 4)
  return Array.from({ length: end - start + 1 }, (_, index) => start + index)
})
const hasPages = computed(() => props.total > props.pageSize)

function go(page: number): void {
  const next = Math.min(Math.max(page, 1), pageCount.value)
  if (next !== props.page) emit('change', next)
}
</script>

<template>
  <nav v-if="hasPages" class="pagination" :aria-label="label">
    <span class="pagination-summary">共 {{ total }} 项 · 第 {{ page }} / {{ pageCount }} 页</span>
    <div class="pagination-controls">
      <button class="button secondary small" type="button" :disabled="page <= 1" @click="go(1)">首页</button>
      <button class="button secondary small" type="button" :disabled="page <= 1" @click="go(page - 1)">上一页</button>
      <button
        v-for="item in visiblePages"
        :key="item"
        class="button small pagination-page"
        :class="{ active: item === page }"
        type="button"
        :aria-current="item === page ? 'page' : undefined"
        @click="go(item)"
      >
        {{ item }}
      </button>
      <button class="button secondary small" type="button" :disabled="page >= pageCount" @click="go(page + 1)">下一页</button>
      <button class="button secondary small" type="button" :disabled="page >= pageCount" @click="go(pageCount)">末页</button>
    </div>
  </nav>
</template>
