import { defineStore } from 'pinia'
import { ref } from 'vue'

import { configurationApi } from '../api/client'
import type {
  ConfigurationSection,
  ConfigurationSnapshot,
  ConfigurationTestResult,
  ConfigurationUpdateRequest,
  PtSiteArchitecture,
} from '../types'

export const useConfigurationStore = defineStore('configuration', () => {
  const data = ref<ConfigurationSnapshot | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let generation = 0

  async function refresh(): Promise<void> {
    const requestGeneration = ++generation
    loading.value = true
    error.value = null
    try {
      const response = await configurationApi.get()
      if (requestGeneration === generation) data.value = response
    } catch (caught) {
      if (requestGeneration === generation) {
        error.value = caught instanceof Error ? caught.message : '加载连接配置失败'
      }
    } finally {
      if (requestGeneration === generation) loading.value = false
    }
  }

  async function update(payload: ConfigurationUpdateRequest): Promise<ConfigurationSnapshot> {
    const snapshot = await configurationApi.update(payload)
    data.value = snapshot
    return snapshot
  }

  async function testConnection(
    section: ConfigurationSection,
    architecture?: PtSiteArchitecture,
  ): Promise<ConfigurationTestResult> {
    if (section === 'nextfind') return configurationApi.testNextFind()
    if (section === 'tmdb') return configurationApi.testTmdb()
    if (section === 'pt_site') {
      if (!architecture) throw new Error('缺少 PT 站点架构')
      return configurationApi.testPtSite(architecture)
    }
    return configurationApi.testQbittorrent()
  }

  return { data, loading, error, refresh, update, testConnection }
})
