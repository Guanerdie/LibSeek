import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import App from '../src/App.vue'
import router from '../src/router'
import { useAuthStore } from '../src/stores/auth'

beforeEach(() => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.initialized = true
  auth.principal = { username: 'admin-user', role: 'admin' }
})

describe('authenticated navigation', () => {
  it('registers login and qBittorrent routes and exposes protected navigation', async () => {
    expect(router.resolve('/login').matched).toHaveLength(1)
    expect(router.resolve('/qbittorrent').matched).toHaveLength(1)
    expect(router.resolve('/executions/execution-1').matched).toHaveLength(1)
    expect(router.resolve('/download-jobs/job-1').matched).toHaveLength(1)
    expect(router.resolve('/automation').matched).toHaveLength(1)
    await router.push('/')

    const wrapper = mount(App, {
      global: {
        plugins: [router],
        stubs: {
          RouterView: true,
          RouterLink: {
            props: ['to'],
            template: '<a :href="to"><slot /></a>',
          },
        },
      },
    })
    expect(wrapper.get('a[href="/qbittorrent"]').text()).toContain('qB 状态')
    expect(wrapper.get('a[href="/executions"]').text()).toContain('下载执行')
    expect(wrapper.get('a[href="/download-jobs"]').text()).toContain('下载任务')
    expect(wrapper.get('a[href="/automation"]').text()).toContain('自动化')
    expect(wrapper.text()).toContain('admin-user')
    expect(wrapper.text()).toContain('管理员')
    expect(wrapper.text()).toContain('第六阶段')
  })

  it('guards protected routes and accepts only safe internal login redirects', async () => {
    const auth = useAuthStore()
    auth.principal = null

    await router.push('/media')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.redirect).toBe('/media')

    auth.principal = { username: 'admin-user', role: 'admin' }
    await router.push('/login?redirect=/qbittorrent')
    expect(router.currentRoute.value.path).toBe('/qbittorrent')

    await router.push('/login?redirect=https://example.invalid')
    expect(router.currentRoute.value.path).toBe('/')
  })
})
