import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '../src/stores/auth'
import type { ConfigurationSnapshot, DownloadJob, MediaItem, SystemStatus } from '../src/types'
import ConfigurationView from '../src/views/ConfigurationView.vue'
import SystemView from '../src/views/SystemView.vue'

const mocks = vi.hoisted(() => ({
  systemStatus: vi.fn(),
  mediaList: vi.fn(),
  downloadJobList: vi.fn(),
  discoveryCreate: vi.fn(),
  discoveryGet: vi.fn(),
  configurationGet: vi.fn(),
  configurationUpdate: vi.fn(),
  configurationTestNextFind: vi.fn(),
  configurationTestTmdb: vi.fn(),
  configurationTestPtSite: vi.fn(),
  configurationTestQbittorrent: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  ApiError: class MockApiError extends Error {},
  setApiCsrfToken: vi.fn(),
  authApi: {},
  systemApi: { status: mocks.systemStatus },
  mediaApi: { list: mocks.mediaList },
  downloadJobApi: { list: mocks.downloadJobList },
  discoveryApi: { create: mocks.discoveryCreate, get: mocks.discoveryGet },
  configurationApi: {
    get: mocks.configurationGet,
    update: mocks.configurationUpdate,
    testNextFind: mocks.configurationTestNextFind,
    testTmdb: mocks.configurationTestTmdb,
    testPtSite: mocks.configurationTestPtSite,
    testQbittorrent: mocks.configurationTestQbittorrent,
  },
}))

const systemStatus: SystemStatus = {
  api: { healthy: true, message: 'API 运行正常', checked_at: '2026-08-11T00:00:00Z' },
  worker: { healthy: true, message: 'Worker 运行正常', checked_at: '2026-08-11T00:00:00Z' },
  postgres: { healthy: true, message: 'PostgreSQL 正常', checked_at: '2026-08-11T00:00:00Z' },
  nextfind_configured: true,
  tmdb_configured: true,
  tmdb_live_enabled: true,
  pt_site_architecture: 'avistaz',
  pt_site_label: 'PT 站点',
  pt_site_configured: true,
  pt_site_runtime_supported: true,
  pt_site_search_ready: true,
  pt_site_status: '只读搜索已启用',
  avistaz_configured: true,
  avistaz_live_enabled: true,
  avistaz_status: '只读搜索已启用',
  qb_configured: true,
  qb_read_only_enabled: true,
  qb_status: '只读已启用',
  download_control_plane_enabled: false,
  download_executor_enabled: false,
  avistaz_torrent_fetch_enabled: false,
  qb_write_enabled: false,
  download_monitor_enabled: false,
  automation_engine_enabled: false,
}

const mediaItems: MediaItem[] = [
  {
    id: 'media-review',
    source: 'nextfind',
    source_item_id: 'source-review',
    media_type: 'tv',
    tmdb_id: null,
    title: '待确认剧集',
    original_title: null,
    year: 2026,
    country_codes: null,
    poster_path: null,
    raw_type: 'tv',
    local_episodes: 2,
    total_episodes: null,
    aired_episodes: null,
    missing_episodes: null,
    discovery_status: 'MISSING',
    identity_confidence: 'NEEDS_CONFIRMATION',
    metadata_status: 'PENDING',
    workflow_status: 'IDENTITY_REVIEW',
    discovered_at: '2026-08-11T00:00:00Z',
    updated_at: '2026-08-11T00:00:00Z',
  },
  {
    id: 'media-confirmed',
    source: 'nextfind',
    source_item_id: 'source-confirmed',
    media_type: 'movie',
    tmdb_id: 42,
    title: '已确认电影',
    original_title: 'Confirmed Movie',
    year: 2025,
    country_codes: null,
    poster_path: null,
    raw_type: 'movie',
    local_episodes: null,
    total_episodes: null,
    aired_episodes: null,
    missing_episodes: null,
    discovery_status: 'MISSING',
    identity_confidence: 'HIGH',
    metadata_status: 'RESOLVED',
    workflow_status: 'TORRENT_REVIEW',
    discovered_at: '2026-08-11T00:00:00Z',
    updated_at: '2026-08-11T00:00:00Z',
  },
]

const configurationSnapshot: ConfigurationSnapshot = {
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
    username: '',
    password_configured: false,
    pid_configured: false,
    configured: false,
    runtime_supported: true,
    search_ready: false,
  },
  pt_sites: {
    avistaz: {
      architecture: 'avistaz',
      base_url: 'https://avistaz.to',
      username: '',
      password_configured: false,
      pid_configured: false,
      configured: false,
      runtime_supported: true,
      search_ready: false,
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
    {
      architecture: 'nexusphp',
      label: 'NexusPHP',
      runtime_supported: false,
      connection_test_supported: true,
      fields: [],
    },
  ],
  qbittorrent: {
    url: '',
    username: '',
    save_path: '',
    category: '',
    configured: false,
    allow_insecure_http: false,
  },
  configuration_complete: false,
}

const downloadJob: DownloadJob = {
  id: 'job-1',
  execution_id: 'execution-1',
  approval_id: 'approval-1',
  media_item_id: 'media-confirmed',
  status: 'DOWNLOADING',
  release_title: 'Confirmed Movie 2025 1080p WEB-DL',
  info_hash_v1: 'a'.repeat(40),
  info_hash_v2: null,
  save_path_ref: 'media-library',
  category: 'media',
  size_bytes: 1024,
  file_count: 1,
  progress: 0.5,
  download_speed_bps: 100,
  upload_speed_bps: 10,
  downloaded_bytes: 512,
  uploaded_bytes: 50,
  ratio: 0.1,
  hnr_status: 'UNKNOWN',
  started_at: '2026-08-11T00:00:00Z',
  completed_at: null,
  last_seen_at: '2026-08-11T00:01:00Z',
  error_code: null,
  error_message: null,
  created_at: '2026-08-11T00:00:00Z',
  updated_at: '2026-08-11T00:01:00Z',
}

const RouterLinkStub = {
  props: ['to'],
  template: '<a :href="to"><slot /></a>',
}

function mountView(component: typeof SystemView | typeof ConfigurationView) {
  return mount(component, {
    global: { stubs: { RouterLink: RouterLinkStub } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  mocks.systemStatus.mockResolvedValue(systemStatus)
  mocks.mediaList.mockResolvedValue({ items: mediaItems, page: 1, page_size: 5, total: 8 })
  mocks.downloadJobList.mockResolvedValue({
    items: [downloadJob],
    page: 1,
    page_size: 5,
    total: 2,
  })
  mocks.discoveryCreate.mockResolvedValue({
    id: 'run-1',
    source: 'nextfind',
    status: 'PENDING',
    started_at: null,
    finished_at: null,
    discovered_count: 0,
    created_count: 0,
    updated_count: 0,
    error_code: null,
    error_message: null,
    created_at: '2026-08-11T00:00:00Z',
    deduplicated: false,
  })
  mocks.discoveryGet.mockResolvedValue({
    id: 'run-1',
    source: 'nextfind',
    status: 'SUCCEEDED',
    started_at: '2026-08-11T00:00:00Z',
    finished_at: '2026-08-11T00:00:01Z',
    discovered_count: 1,
    created_count: 1,
    updated_count: 0,
    error_code: null,
    error_message: null,
    created_at: '2026-08-11T00:00:00Z',
  })
  mocks.configurationGet.mockResolvedValue(configurationSnapshot)
  mocks.configurationUpdate.mockResolvedValue(configurationSnapshot)
  mocks.configurationTestNextFind.mockResolvedValue({
    target: 'nextfind',
    healthy: true,
    error_code: null,
    message: 'NextFind 登录验证成功',
  })
  mocks.configurationTestTmdb.mockResolvedValue({
    target: 'tmdb',
    healthy: true,
    error_code: null,
    message: 'TMDB Access Token 验证成功',
  })
  mocks.configurationTestPtSite.mockResolvedValue({
    target: 'pt_site',
    healthy: true,
    error_code: null,
    message: 'AvistaZ 只读认证成功',
  })
  mocks.configurationTestQbittorrent.mockResolvedValue({
    target: 'qbittorrent',
    healthy: true,
    error_code: null,
    message: 'qBittorrent 登录验证成功',
  })
  const auth = useAuthStore()
  auth.initialized = true
  auth.principal = { username: 'operator-user', role: 'operator' }
})

describe('dashboard and configuration views', () => {
  it('renders one workbench with gated discovery and workflow-specific actions', async () => {
    const wrapper = mountView(SystemView)
    await flushPromises()

    expect(wrapper.text()).toContain('运行与配置')
    expect(wrapper.text()).toContain('未入库影视8')
    expect(wrapper.text()).toContain('下载任务2')
    expect(wrapper.get('a[href="/media/media-review/identity"]').text()).toContain('确认影视信息')
    expect(wrapper.get('a[href="/media/media-confirmed/torrents"]').text()).toContain('选择 PT 种子')
    expect(wrapper.get('a[href="/download-jobs/job-1"]').text()).toContain('查看总结')
    expect(wrapper.get('.dashboard-primary-action .primary').attributes('disabled')).toBeUndefined()

    await wrapper.get('.dashboard-primary-action .primary').trigger('click')
    await flushPromises()

    expect(mocks.discoveryCreate).toHaveBeenCalledTimes(1)
    expect(mocks.mediaList).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('发现完成')
    expect(wrapper.text()).toContain('下载执行门禁')
    expect(wrapper.text()).toContain('保持关闭')
    expect(wrapper.text()).toContain('PT 站点')
    expect(wrapper.text()).not.toContain('AvistaZ')
    expect(wrapper.text()).toContain('只读集成就绪4 / 4')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('disables discovery for viewer and for missing NextFind configuration', async () => {
    const auth = useAuthStore()
    auth.principal = { username: 'viewer-user', role: 'viewer' }
    const viewerWrapper = mountView(SystemView)
    await flushPromises()
    expect(viewerWrapper.get('.dashboard-primary-action .primary').attributes('disabled')).toBeDefined()
    expect(viewerWrapper.text()).toContain('当前角色只读')
    viewerWrapper.unmount()

    setActivePinia(createPinia())
    const nextAuth = useAuthStore()
    nextAuth.initialized = true
    nextAuth.principal = { username: 'admin-user', role: 'admin' }
    mocks.systemStatus.mockResolvedValueOnce({ ...systemStatus, nextfind_configured: false })
    const unconfiguredWrapper = mountView(SystemView)
    await flushPromises()
    expect(unconfiguredWrapper.get('.dashboard-primary-action .primary').attributes('disabled')).toBeDefined()
    expect(unconfiguredWrapper.text()).toContain('请先完成 NextFind 本机配置')
  })

  it('labels enabled download write switches without claiming executor readiness', async () => {
    mocks.systemStatus.mockResolvedValueOnce({
      ...systemStatus,
      download_control_plane_enabled: true,
      download_executor_enabled: true,
      avistaz_torrent_fetch_enabled: true,
      qb_write_enabled: true,
    })

    const wrapper = mountView(SystemView)
    await flushPromises()

    expect(wrapper.text()).toContain('下载写入开关已开启')
    expect(wrapper.text()).toContain('不代表凭据、目标策略或执行器心跳已经完整就绪')
    expect(wrapper.text()).not.toContain('人工下载已启用')
  })

  it('saves only NextFind and clears only its submitted secret', async () => {
    const wrapper = mountView(ConfigurationView)
    await flushPromises()

    expect(wrapper.text()).toContain('连接配置')
    expect(wrapper.text()).toContain('PostgreSQL')
    expect(wrapper.text()).toContain('下载执行门禁')
    expect(wrapper.text()).toContain('qBittorrent 写入')
    expect((wrapper.get('input[name="nextfind_base_url"]').element as HTMLInputElement).value).toBe(
      'https://nextfind.example',
    )
    expect((wrapper.get('input[name="nextfind_username"]').element as HTMLInputElement).value).toBe('nextfind-user')
    expect(wrapper.text()).toContain('2 / 4')

    await wrapper.get('input[name="nextfind_password"]').setValue('nextfind-secret')
    await wrapper.get('input[name="tmdb_token"]').setValue('tmdb-secret')
    await wrapper.get('input[name="avistaz_pid"]').setValue('avistaz-pid-secret')
    await wrapper.get('input[name="qb_password"]').setValue('qb-secret')
    await wrapper
      .get('form[aria-labelledby="nextfind-config-title"]')
      .trigger('submit')
    await flushPromises()

    expect(mocks.configurationUpdate).toHaveBeenCalledTimes(1)
    expect(mocks.configurationUpdate.mock.calls[0]?.[0]).toEqual({
      nextfind: {
        base_url: 'https://nextfind.example',
        username: 'nextfind-user',
        password: 'nextfind-secret',
      },
    })
    expect((wrapper.get('input[name="nextfind_password"]').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('input[name="tmdb_token"]').element as HTMLInputElement).value).toBe('tmdb-secret')
    expect((wrapper.get('input[name="avistaz_pid"]').element as HTMLInputElement).value).toBe('avistaz-pid-secret')
    expect((wrapper.get('input[name="qb_password"]').element as HTMLInputElement).value).toBe('qb-secret')
    expect(wrapper.text()).toContain('后端已确认写入本机持久配置，尚未测试外部连接')
    expect(wrapper.text()).toContain('保存成功')
    expect(wrapper.text()).toContain('目标：https://nextfind.example')
    expect(wrapper.find('a[href="/adapters"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/automation"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/qbittorrent"]').exists()).toBe(true)
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('switches PT architecture, clears temporary secrets, and submits only the active branch', async () => {
    const savedNexus = {
      architecture: 'nexusphp' as const,
      site_id: 'example-nexus',
      display_name: 'Example Nexus',
      base_url: 'https://tracker.example.com',
      profile_id: null,
      cookie_configured: true,
      passkey_configured: true,
      configured: true,
      runtime_supported: false,
      search_ready: false,
    }
    mocks.configurationUpdate.mockResolvedValueOnce({
      ...configurationSnapshot,
      pt_site: savedNexus,
      pt_sites: { ...configurationSnapshot.pt_sites, nexusphp: savedNexus },
    })
    const wrapper = mountView(ConfigurationView)
    await flushPromises()

    await wrapper.get('input[name="avistaz_password"]').setValue('avistaz-not-sent')
    await wrapper.get('select[name="pt_architecture"]').setValue('nexusphp')
    expect(wrapper.find('input[name="avistaz_password"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('保存后待适配')
    expect(wrapper.text()).toContain('不会搜索、取种或下载')

    await wrapper.get('input[name="nexus_site_id"]').setValue('example-nexus')
    await wrapper.get('input[name="nexus_display_name"]').setValue('Example Nexus')
    await wrapper.get('input[name="nexus_base_url"]').setValue('https://tracker.example.com')
    await wrapper.get('input[name="nexus_cookie"]').setValue('nexus-cookie')
    await wrapper.get('input[name="nexus_passkey"]').setValue('nexus-passkey')

    await wrapper.get('select[name="pt_architecture"]').setValue('avistaz')
    expect((wrapper.get('input[name="avistaz_password"]').element as HTMLInputElement).value).toBe('')
    await wrapper.get('select[name="pt_architecture"]').setValue('nexusphp')
    expect((wrapper.get('input[name="nexus_cookie"]').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('input[name="nexus_passkey"]').element as HTMLInputElement).value).toBe('')

    await wrapper.get('input[name="nexus_cookie"]').setValue('nexus-cookie')
    await wrapper.get('input[name="nexus_passkey"]').setValue('nexus-passkey')
    await wrapper
      .get('form[aria-labelledby="pt-config-title"]')
      .trigger('submit')
    await flushPromises()

    const payload = mocks.configurationUpdate.mock.calls[0]?.[0]
    expect(payload).toEqual({
      pt_site: {
        architecture: 'nexusphp',
        site_id: 'example-nexus',
        display_name: 'Example Nexus',
        base_url: 'https://tracker.example.com',
        cookie: 'nexus-cookie',
        passkey: 'nexus-passkey',
      },
    })
    expect((wrapper.get('input[name="nexus_cookie"]').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('input[name="nexus_passkey"]').element as HTMLInputElement).value).toBe('')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('restores an inactive saved PT site before switching back and saving', async () => {
    mocks.configurationGet.mockResolvedValueOnce({
      ...configurationSnapshot,
      pt_site: {
        architecture: 'nexusphp',
        site_id: 'saved-nexus',
        display_name: 'Saved Nexus',
        base_url: 'https://nexus.example.test',
        profile_id: null,
        cookie_configured: true,
        passkey_configured: false,
        configured: true,
        runtime_supported: false,
        search_ready: false,
      },
      pt_sites: {
        avistaz: {
          architecture: 'avistaz',
          base_url: 'https://saved-avistaz.example.test',
          username: 'saved-user',
          password_configured: true,
          pid_configured: true,
          configured: true,
          runtime_supported: true,
          search_ready: false,
        },
        nexusphp: {
          architecture: 'nexusphp',
          site_id: 'saved-nexus',
          display_name: 'Saved Nexus',
          base_url: 'https://nexus.example.test',
          profile_id: null,
          cookie_configured: true,
          passkey_configured: false,
          configured: true,
          runtime_supported: false,
          search_ready: false,
        },
      },
    })
    const wrapper = mountView(ConfigurationView)
    await flushPromises()

    await wrapper.get('select[name="pt_architecture"]').setValue('avistaz')
    expect((wrapper.get('input[name="avistaz_base_url"]').element as HTMLInputElement).value).toBe(
      'https://saved-avistaz.example.test',
    )
    expect((wrapper.get('input[name="avistaz_username"]').element as HTMLInputElement).value).toBe(
      'saved-user',
    )
    expect(wrapper.get('input[name="avistaz_password"]').attributes('placeholder')).toContain(
      '留空则保持不变',
    )

    await wrapper
      .get('form[aria-labelledby="pt-config-title"]')
      .trigger('submit')
    await flushPromises()
    expect(mocks.configurationUpdate.mock.calls[0]?.[0].pt_site).toEqual({
      architecture: 'avistaz',
      base_url: 'https://saved-avistaz.example.test',
      username: 'saved-user',
    })
  })

  it('keeps submitted secrets available for correction when saving fails', async () => {
    mocks.configurationUpdate.mockRejectedValueOnce(new Error('配置保存失败'))
    const wrapper = mountView(ConfigurationView)
    await flushPromises()

    for (const [name, value] of [
      ['nextfind_password', 'nextfind-secret'],
      ['tmdb_token', 'tmdb-secret'],
      ['avistaz_password', 'avistaz-secret'],
      ['avistaz_pid', 'pid-secret'],
      ['qb_password', 'qb-secret'],
    ]) {
      await wrapper.get(`input[name="${name}"]`).setValue(value)
    }
    await wrapper
      .get('form[aria-labelledby="nextfind-config-title"]')
      .trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('配置保存失败')
    expect((wrapper.get('input[name="nextfind_password"]').element as HTMLInputElement).value).toBe('nextfind-secret')
    expect((wrapper.get('input[name="tmdb_token"]').element as HTMLInputElement).value).toBe('tmdb-secret')
    expect((wrapper.get('input[name="avistaz_password"]').element as HTMLInputElement).value).toBe('avistaz-secret')
    expect((wrapper.get('input[name="avistaz_pid"]').element as HTMLInputElement).value).toBe('pid-secret')
    expect((wrapper.get('input[name="qb_password"]').element as HTMLInputElement).value).toBe('qb-secret')
  })

  it('tests saved configuration and saves dirty input before testing again', async () => {
    const wrapper = mountView(ConfigurationView)
    await flushPromises()

    const nextfindForm = wrapper.get('form[aria-labelledby="nextfind-config-title"]')
    const testButton = nextfindForm.get('button.secondary')
    expect(testButton.attributes('disabled')).toBeUndefined()

    await testButton.trigger('click')
    await flushPromises()

    expect(mocks.configurationTestNextFind).toHaveBeenCalledTimes(1)
    expect(nextfindForm.text()).toContain('NextFind 登录验证成功')
    expect(nextfindForm.text()).toContain('真实连接成功')
    expect(nextfindForm.text()).toContain('目标：https://nextfind.example')

    await wrapper.get('input[name="nextfind_username"]').setValue('changed-user')
    expect(nextfindForm.get('button.secondary').attributes('disabled')).toBeUndefined()
    expect(nextfindForm.get('button.secondary').text()).toContain('保存并测试')
    expect(nextfindForm.text()).toContain('测试时会先保存当前输入')
    expect(nextfindForm.text()).not.toContain('NextFind 登录验证成功')

    await wrapper.get('input[name="nextfind_password"]').setValue('replacement-secret')
    await nextfindForm.get('button.secondary').trigger('click')
    await flushPromises()

    expect(mocks.configurationUpdate).toHaveBeenCalledWith({
      nextfind: {
        base_url: 'https://nextfind.example',
        username: 'changed-user',
        password: 'replacement-secret',
      },
    })
    expect(mocks.configurationTestNextFind).toHaveBeenCalledTimes(2)
    expect((wrapper.get('input[name="nextfind_password"]').element as HTMLInputElement).value).toBe('')
  })

  it('saves a previously unconfigured TMDB token and then runs the real test endpoint', async () => {
    mocks.configurationGet.mockResolvedValueOnce({
      ...configurationSnapshot,
      tmdb: { configured: false },
    })
    mocks.configurationUpdate.mockResolvedValueOnce(configurationSnapshot)
    const wrapper = mountView(ConfigurationView)
    await flushPromises()

    const tmdbForm = wrapper.get('form[aria-labelledby="tmdb-config-title"]')
    expect(tmdbForm.get('button.secondary').text()).toContain('保存并测试')
    await wrapper.get('input[name="tmdb_token"]').setValue('tmdb-secret')
    await tmdbForm.get('button.secondary').trigger('click')
    await flushPromises()

    expect(mocks.configurationUpdate).toHaveBeenCalledWith({ tmdb: { token: 'tmdb-secret' } })
    expect(mocks.configurationTestTmdb).toHaveBeenCalledTimes(1)
    expect(tmdbForm.text()).toContain('TMDB Access Token 验证成功')
    expect(tmdbForm.text()).toContain('目标：https://api.themoviedb.org')
    expect((wrapper.get('input[name="tmdb_token"]').element as HTMLInputElement).value).toBe('')
  })

  it('clears a secret after save succeeds even when the subsequent connection test fails', async () => {
    mocks.configurationGet.mockResolvedValueOnce({
      ...configurationSnapshot,
      tmdb: { configured: false },
    })
    mocks.configurationUpdate.mockResolvedValueOnce(configurationSnapshot)
    mocks.configurationTestTmdb.mockResolvedValueOnce({
      target: 'tmdb',
      healthy: false,
      error_code: 'AUTH_FAILED',
      message: 'TMDB Access Token 无效',
    })
    const wrapper = mountView(ConfigurationView)
    await flushPromises()

    const tmdbForm = wrapper.get('form[aria-labelledby="tmdb-config-title"]')
    await wrapper.get('input[name="tmdb_token"]').setValue('tmdb-secret')
    await tmdbForm.get('button.secondary').trigger('click')
    await flushPromises()

    expect(mocks.configurationUpdate).toHaveBeenCalledTimes(1)
    expect(mocks.configurationTestTmdb).toHaveBeenCalledTimes(1)
    expect((wrapper.get('input[name="tmdb_token"]').element as HTMLInputElement).value).toBe('')
    expect(tmdbForm.text()).toContain('真实连接失败')
    expect(tmdbForm.text()).toContain('错误码：AUTH_FAILED')
  })

  it('reconciles the saved section with normalized backend values', async () => {
    mocks.configurationUpdate.mockResolvedValueOnce({
      ...configurationSnapshot,
      nextfind: {
        ...configurationSnapshot.nextfind,
        base_url: 'https://nextfind.example.test',
      },
    })
    const wrapper = mountView(ConfigurationView)
    await flushPromises()

    await wrapper
      .get('input[name="nextfind_base_url"]')
      .setValue('https://nextfind.example.test/')
    await wrapper.get('input[name="nextfind_password"]').setValue('replacement-secret')
    await wrapper
      .get('form[aria-labelledby="nextfind-config-title"]')
      .trigger('submit')
    await flushPromises()

    const nextfindForm = wrapper.get('form[aria-labelledby="nextfind-config-title"]')
    expect((wrapper.get('input[name="nextfind_base_url"]').element as HTMLInputElement).value).toBe(
      'https://nextfind.example.test',
    )
    expect(wrapper.get('input[name="nextfind_password"]').attributes('required')).toBeUndefined()
    expect(nextfindForm.get('button.secondary').attributes('disabled')).toBeUndefined()
  })

  it('allows a real NexusPHP Cookie test while keeping search marked unsupported', async () => {
    const wrapper = mountView(ConfigurationView)
    await flushPromises()

    await wrapper.get('select[name="pt_architecture"]').setValue('nexusphp')
    const ptForm = wrapper.get('form[aria-labelledby="pt-config-title"]')

    expect(ptForm.get('button.secondary').attributes('disabled')).toBeUndefined()
    expect(ptForm.get('button.secondary').text()).toContain('保存并测试')
    expect(ptForm.text()).toContain('保存后验证 Cookie 会话；搜索功能待适配')
    expect(ptForm.text()).toContain('不会搜索、取种或下载')

    await wrapper.get('input[name="nexus_site_id"]').setValue('example-nexus')
    await wrapper.get('input[name="nexus_display_name"]').setValue('Example Nexus')
    await wrapper.get('input[name="nexus_base_url"]').setValue('https://tracker.example.com')
    await wrapper.get('input[name="nexus_cookie"]').setValue('uid=1; pass=secret')
    await ptForm.get('button.secondary').trigger('click')
    await flushPromises()

    expect(mocks.configurationUpdate).toHaveBeenCalledWith({
      pt_site: {
        architecture: 'nexusphp',
        site_id: 'example-nexus',
        display_name: 'Example Nexus',
        base_url: 'https://tracker.example.com',
        cookie: 'uid=1; pass=secret',
      },
    })
    expect(mocks.configurationTestPtSite).toHaveBeenCalledWith('nexusphp')
  })
})
