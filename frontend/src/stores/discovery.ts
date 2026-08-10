import { defineStore } from 'pinia'
import { ref } from 'vue'

import { discoveryApi } from '../api/client'
import type { DiscoveryRun } from '../types'

export const useDiscoveryStore = defineStore('discovery', () => {
  const runs = ref<DiscoveryRun[]>([])
  const selected = ref<DiscoveryRun | null>(null)
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const loading = ref(false)
  const creating = ref(false)
  const error = ref<string | null>(null)
  const notice = ref<string | null>(null)
  let listGeneration = 0
  let detailGeneration = 0

  async function load(): Promise<void> {
    const requestGeneration = ++listGeneration
    runs.value = []
    total.value = 0
    error.value = null
    loading.value = true
    try {
      const response = await discoveryApi.list(page.value, pageSize.value)
      if (requestGeneration === listGeneration) {
        runs.value = response.items
        total.value = response.total
      }
    } catch (caught) {
      if (requestGeneration === listGeneration) {
        error.value = caught instanceof Error ? caught.message : '加载发现任务失败'
      }
    } finally {
      if (requestGeneration === listGeneration) loading.value = false
    }
  }

  async function refresh(): Promise<void> {
    const selectedId = selected.value?.id
    notice.value = null
    await load()
    if (selectedId) await selectRun(selectedId)
  }

  async function selectRun(id: string): Promise<void> {
    const requestGeneration = ++detailGeneration
    selected.value = null
    error.value = null
    try {
      const response = await discoveryApi.get(id)
      if (requestGeneration === detailGeneration) selected.value = response
    } catch (caught) {
      if (requestGeneration === detailGeneration) {
        error.value = caught instanceof Error ? caught.message : '加载任务详情失败'
      }
    }
  }

  async function create(): Promise<void> {
    creating.value = true
    error.value = null
    notice.value = null
    try {
      const run = await discoveryApi.create()
      await load()
      await selectRun(run.id)
      notice.value = run.deduplicated
        ? '已有发现任务正在运行；可点击“刷新进度”查看 Worker 的最新结果'
        : '发现任务已创建；任务异步执行，可点击“刷新进度”查看最新结果'
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : '创建发现任务失败'
    } finally {
      creating.value = false
    }
  }

  return {
    runs,
    selected,
    total,
    page,
    pageSize,
    loading,
    creating,
    error,
    notice,
    load,
    refresh,
    selectRun,
    create,
  }
})
