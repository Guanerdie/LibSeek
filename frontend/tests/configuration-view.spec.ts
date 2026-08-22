import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '../src/stores/auth'
import type { ConfigurationSnapshot } from '../src/types'
import ConfigurationView from '../src/views/ConfigurationView.vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  update: vi.fn(),
  testNextFind: vi.fn(),
  testTmdb: vi.fn(),
  testPtSite: vi.fn(),
  testQbittorrent: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  configurationApi: mocks,
  authApi: {},
  setApiCsrfToken: vi.fn(),
}))

const snapshot: ConfigurationSnapshot = {
  nextfind: {
    base_url: 'https://nextfind.example',
    username: 'nextfind-user',
    password_configured: true,
    configured: true,
  },
  tmdb: { configured: true },
  pt_site: {
    architecture: 'avistaz',
    base_url: 'https://avistaz.to',
    username: 'avistaz-user',
    password_configured: true,
    pid_configured: true,
    configured: true,
    runtime_supported: true,
    search_ready: true,
  },
  pt_sites: {
    avistaz: {
      architecture: 'avistaz',
      base_url: 'https://avistaz.to',
      username: 'avistaz-user',
      password_configured: true,
      pid_configured: true,
      configured: true,
      runtime_supported: true,
      search_ready: true,
    },
    nexusphp: null,
  },
  pt_site_architectures: [
    {
      architecture: 'avistaz',
      label: 'AvistaZ',
      runtime_supported: true,
      connection_test_supported: true,
      fields: [],
    },
  ],
  qbittorrent: {
    url: 'https://qb.internal',
    username: 'qb-user',
    save_path: '/downloads',
    category: 'unin',
    configured: true,
    allow_insecure_http: false,
  },
  configuration_complete: true,
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  mocks.get.mockResolvedValue(snapshot)
  mocks.update.mockResolvedValue(snapshot)
  const auth = useAuthStore()
  auth.initialized = true
  auth.principal = { username: 'admin', role: 'admin' }
})

describe('connection settings', () => {
  it('shows only the four daily connections without retired runtime gates', async () => {
    const wrapper = mount(ConfigurationView)
    await flushPromises()

    expect(wrapper.findAll('.integration-config-section')).toHaveLength(4)
    expect(wrapper.text()).toContain('NextFind')
    expect(wrapper.text()).toContain('TMDB')
    expect(wrapper.text()).toContain('PT 站点')
    expect(wrapper.text()).toContain('qBittorrent')
    expect(wrapper.text()).not.toContain('下载执行门禁')
    expect(wrapper.text()).not.toContain('PostgreSQL')
    expect(wrapper.get('input[name="qb_save_path"]').attributes('required')).toBeDefined()
    expect(wrapper.get('input[name="qb_category"]').attributes('required')).toBeDefined()
  })

  it('saves one connection without resubmitting unrelated secrets', async () => {
    const wrapper = mount(ConfigurationView)
    await flushPromises()
    await wrapper.get('input[name="nextfind_password"]').setValue('new-password')
    await wrapper.get('form[aria-labelledby="nextfind-config-title"]').trigger('submit')
    await flushPromises()

    expect(mocks.update).toHaveBeenCalledWith({
      nextfind: {
        base_url: 'https://nextfind.example',
        username: 'nextfind-user',
        password: 'new-password',
      },
    })
  })
})
