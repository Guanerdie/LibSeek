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
  testOutboundProxy: vi.fn(),
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
  outbound_proxy: {
    url: 'http://proxy.internal:7890',
    username: 'proxy-user',
    password_configured: true,
    configured: true,
  },
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
  it('shows the four daily connections and optional outbound proxy without retired gates', async () => {
    const wrapper = mount(ConfigurationView)
    await flushPromises()

    // Five connection sections plus the account password form.
    expect(wrapper.findAll('.integration-config-section')).toHaveLength(6)
    expect(wrapper.find('#password-config-title').exists()).toBe(true)
    expect(wrapper.text()).toContain('NextFind')
    expect(wrapper.text()).toContain('TMDB')
    expect(wrapper.text()).toContain('PT 站点')
    expect(wrapper.text()).toContain('qBittorrent')
    expect(wrapper.text()).toContain('出站代理')
    expect(wrapper.text()).not.toContain('下载执行门禁')
    expect(wrapper.text()).not.toContain('PostgreSQL')
    expect(wrapper.get('input[name="qb_save_path"]').attributes('required')).toBeUndefined()
    expect(wrapper.get('input[name="qb_category"]').attributes('required')).toBeUndefined()
    expect(wrapper.get('a[href="https://www.themoviedb.org/settings/api"]').attributes('target')).toBe('_blank')
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

  it('persists outbound proxy secrets without including unrelated configuration', async () => {
    const wrapper = mount(ConfigurationView)
    await flushPromises()
    await wrapper.get('input[name="proxy_password"]').setValue('new-proxy-password')
    await wrapper.get('form[aria-labelledby="proxy-config-title"]').trigger('submit')
    await flushPromises()

    expect(mocks.update).toHaveBeenCalledWith({
      outbound_proxy: {
        url: 'http://proxy.internal:7890',
        username: 'proxy-user',
        password: 'new-proxy-password',
      },
    })
  })
})
