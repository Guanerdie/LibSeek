import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api/client'
import { redirectExpiredSession } from '../src/auth/session'
import { useAuthStore } from '../src/stores/auth'
import LoginView from '../src/views/LoginView.vue'

const mocks = vi.hoisted(() => ({
  setupStatus: vi.fn(),
  setup: vi.fn(),
  csrf: vi.fn(),
  login: vi.fn(),
  changePassword: vi.fn(),
  me: vi.fn(),
  logout: vi.fn(),
  setApiCsrfToken: vi.fn(),
}))

vi.mock('../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return {
    ...actual,
    authApi: {
      setupStatus: mocks.setupStatus,
      setup: mocks.setup,
      csrf: mocks.csrf,
      login: mocks.login,
      changePassword: mocks.changePassword,
      me: mocks.me,
      logout: mocks.logout,
    },
    setApiCsrfToken: mocks.setApiCsrfToken,
  }
})

let pinia: Pinia

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  vi.clearAllMocks()
  mocks.setupStatus.mockResolvedValue({
    admin_initialized: true,
    configuration_complete: true,
  })
})

describe('auth store', () => {
  it('bootstraps CSRF before loading the current principal', async () => {
    const order: string[] = []
    mocks.csrf.mockImplementation(async () => {
      order.push('csrf')
      return { csrf_token: 'bootstrap-token' }
    })
    mocks.me.mockImplementation(async () => {
      order.push('me')
      return { username: 'operator-user', role: 'operator' }
    })

    const store = useAuthStore()
    await store.initialize()

    expect(mocks.setupStatus).toHaveBeenCalledTimes(1)
    expect(order).toEqual(['csrf', 'me'])
    expect(store.principal).toEqual({ username: 'operator-user', role: 'operator' })
    expect(store.csrfToken).toBe('bootstrap-token')
    expect(store.initialized).toBe(true)
    expect(store.error).toBeNull()
  })

  it('stops before CSRF when the local administrator has not been initialized', async () => {
    mocks.setupStatus.mockResolvedValueOnce({
      admin_initialized: false,
      configuration_complete: false,
    })

    const store = useAuthStore()
    await store.initialize()

    expect(store.adminInitialized).toBe(false)
    expect(store.configurationComplete).toBe(false)
    expect(store.principal).toBeNull()
    expect(mocks.csrf).not.toHaveBeenCalled()
    expect(mocks.me).not.toHaveBeenCalled()
  })

  it('treats an anonymous me response as a normal login state and keeps bootstrap CSRF', async () => {
    mocks.csrf.mockResolvedValueOnce({ csrf_token: 'bootstrap-token' })
    mocks.me.mockRejectedValueOnce(new ApiError('AUTH_REQUIRED', '需要登录', 401))

    const store = useAuthStore()
    await store.initialize()

    expect(store.principal).toBeNull()
    expect(store.csrfToken).toBe('bootstrap-token')
    expect(store.error).toBeNull()
  })

  it('logs in without persisting a password and applies inherited roles', async () => {
    mocks.csrf.mockResolvedValueOnce({ csrf_token: 'bootstrap-token' })
    mocks.login.mockResolvedValueOnce({
      username: 'admin-user',
      role: 'admin',
      csrf_token: 'session-token',
    })
    const store = useAuthStore()

    await expect(store.login('admin-user', 'temporary-password')).resolves.toBe(true)

    expect(mocks.login).toHaveBeenCalledWith('admin-user', 'temporary-password', false)
    expect(store.principal).toEqual({ username: 'admin-user', role: 'admin' })
    expect(store.hasRole('viewer')).toBe(true)
    expect(store.hasRole('operator')).toBe(true)
    expect(store.hasRole('admin')).toBe(true)
    expect(Object.keys(store.$state)).not.toContain('password')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('passes the remember choice through to the server', async () => {
    mocks.csrf.mockResolvedValue({ csrf_token: 'csrf-token' })
    mocks.login.mockResolvedValue({
      username: 'admin-user',
      role: 'admin',
      csrf_token: 'session-token',
    })
    const store = useAuthStore()

    await store.login('admin-user', 'temporary-password', true)

    expect(mocks.login).toHaveBeenCalledWith('admin-user', 'temporary-password', true)
  })

  it('keeps the reissued csrf token after a password change', async () => {
    // The server rotates the signing key, so a stale token would break every
    // later mutation.
    mocks.changePassword.mockResolvedValue({
      username: 'admin-user',
      role: 'admin',
      csrf_token: 'rotated-token',
    })
    const store = useAuthStore()

    await expect(store.changePassword('old-pass', 'new-pass-1')).resolves.toBe(true)

    expect(store.csrfToken).toBe('rotated-token')
    // A password must never survive the call, whatever the outcome.
    expect(Object.keys(store.$state)).not.toContain('password')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('keeps the specific setup validation message returned by the backend', async () => {
    mocks.setup.mockRejectedValueOnce(
      new ApiError('API_VALIDATION_ERROR', '管理员密码至少需要 6 位', 422),
    )
    const store = useAuthStore()

    await expect(store.setupAdmin('first-admin', '123456')).resolves.toBe(false)

    expect(store.error).toBe('管理员密码至少需要 6 位')
  })

  it('keeps the local session visible when logout fails for a non-401 error', async () => {
    mocks.logout.mockRejectedValueOnce(new Error('网络不可用'))
    const store = useAuthStore()
    store.principal = { username: 'operator-user', role: 'operator' }
    store.csrfToken = 'session-token'

    await expect(store.logout()).resolves.toBe(false)

    expect(store.principal).toEqual({ username: 'operator-user', role: 'operator' })
    expect(store.csrfToken).toBe('session-token')
    expect(store.error).toContain('网络不可用')
  })

  it('clears in-memory authentication after logout succeeds', async () => {
    mocks.logout.mockResolvedValueOnce(undefined)
    const store = useAuthStore()
    store.principal = { username: 'viewer-user', role: 'viewer' }
    store.csrfToken = 'session-token'

    await expect(store.logout()).resolves.toBe(true)

    expect(store.principal).toBeNull()
    expect(store.csrfToken).toBeNull()
    expect(mocks.setApiCsrfToken).toHaveBeenLastCalledWith(null)
  })
})

describe('expired-session handling', () => {
  it('clears in-memory state and replaces a protected route with login', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/protected', component: { template: '<div />' } },
        { path: '/login', component: LoginView, meta: { public: true } },
      ],
    })
    await router.push('/protected?tab=active')
    const store = useAuthStore()
    store.initialized = true
    store.principal = { username: 'admin-user', role: 'admin' }
    store.csrfToken = 'session-token'

    redirectExpiredSession(store, router)
    await flushPromises()

    expect(store.principal).toBeNull()
    expect(store.csrfToken).toBeNull()
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.redirect).toBe('/protected?tab=active')
  })
})

describe('LoginView', () => {
  function testRouter() {
    return createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/login', component: LoginView },
        { path: '/target', component: { template: '<div>target</div>' } },
        { path: '/configuration', component: { template: '<div>configuration</div>' } },
      ],
    })
  }

  it('clears the password after success and never renders the CSRF token', async () => {
    mocks.csrf.mockResolvedValueOnce({ csrf_token: 'bootstrap-secret' })
    mocks.login.mockResolvedValueOnce({
      username: 'operator-user',
      role: 'operator',
      csrf_token: 'session-secret',
    })
    const router = testRouter()
    await router.push('/login?redirect=/target')
    const auth = useAuthStore()
    auth.adminInitialized = true
    const wrapper = mount(LoginView, { global: { plugins: [pinia, router] } })

    await wrapper.get('input[name="username"]').setValue('operator-user')
    await wrapper.get('input[name="password"]').setValue('temporary-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect((wrapper.get('input[name="password"]').element as HTMLInputElement).value).toBe('')
    expect(router.currentRoute.value.path).toBe('/target')
    expect(wrapper.text()).not.toContain('bootstrap-secret')
    expect(wrapper.text()).not.toContain('session-secret')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('also clears the password and shows a stable error after failed credentials', async () => {
    mocks.csrf.mockResolvedValueOnce({ csrf_token: 'bootstrap-token' })
    mocks.login.mockRejectedValueOnce(new ApiError('AUTH_LOGIN_FAILED', '用户名或密码错误', 401))
    const router = testRouter()
    await router.push('/login')
    const auth = useAuthStore()
    auth.adminInitialized = true
    const wrapper = mount(LoginView, { global: { plugins: [pinia, router] } })

    await wrapper.get('input[name="username"]').setValue('operator-user')
    await wrapper.get('input[name="password"]').setValue('wrong-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect((wrapper.get('input[name="password"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('用户名或密码错误')
    expect(router.currentRoute.value.path).toBe('/login')
  })

  it('explains the six-character minimum before creating the first administrator', async () => {
    const router = testRouter()
    await router.push('/login')
    const auth = useAuthStore()
    auth.adminInitialized = false
    const wrapper = mount(LoginView, { global: { plugins: [pinia, router] } })

    await wrapper.get('input[name="username"]').setValue('first-admin')
    await wrapper.get('input[name="password"]').setValue('12345')
    await wrapper.get('input[name="password_confirmation"]').setValue('12345')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.text()).toContain('管理员密码至少需要 6 位')
    expect(mocks.setup).not.toHaveBeenCalled()
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('creates the first administrator, clears both passwords, and opens configuration', async () => {
    mocks.setup.mockResolvedValueOnce({
      username: 'first-admin',
      role: 'admin',
      csrf_token: 'new-session-secret',
    })
    const router = testRouter()
    await router.push('/login')
    const auth = useAuthStore()
    auth.adminInitialized = false
    const wrapper = mount(LoginView, { global: { plugins: [pinia, router] } })

    expect(wrapper.text()).toContain('创建管理员')
    await wrapper.get('input[name="username"]').setValue('first-admin')
    await wrapper.get('input[name="password"]').setValue('123456')
    await wrapper.get('input[name="password_confirmation"]').setValue('123456')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.setup).toHaveBeenCalledWith('first-admin', '123456')
    expect(auth.principal).toEqual({ username: 'first-admin', role: 'admin' })
    expect(auth.csrfToken).toBe('new-session-secret')
    expect(router.currentRoute.value.path).toBe('/configuration')
    expect(wrapper.text()).not.toContain('new-session-secret')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })
})
