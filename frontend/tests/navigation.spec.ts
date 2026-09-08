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
  it('exposes only the daily routes', async () => {
    expect(router.resolve('/login').matched).toHaveLength(1)
    expect(router.resolve('/configuration').matched).toHaveLength(1)
    expect(router.resolve('/library').matched).toHaveLength(1)
    expect(router.resolve('/library/media-1/resources').matched).toHaveLength(1)
    expect(router.resolve('/downloads').matched).toHaveLength(1)
    expect(router.resolve('/discovery').matched[0]?.path).toBe('/:pathMatch(.*)*')
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/library')

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
    expect(
      wrapper.findAll('.primary-nav-link').map((link) => ({
        href: link.attributes('href'),
        text: link.text(),
      })),
    ).toEqual([
      { href: '/library', text: '▦缺失' },
      { href: '/downloads', text: '↓下载' },
      { href: '/automation', text: '⟳自动化' },
      { href: '/monitoring', text: '◔监控' },
      { href: '/configuration', text: '⚙设置' },
    ])
    expect(wrapper.find('.nav-disclosure').exists()).toBe(false)
    expect(wrapper.find('a[href="/"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/discovery"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/adapters"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/approvals"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/media-imports"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/qbittorrent"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('admin-user')
    expect(wrapper.text()).toContain('管理员')
    expect(wrapper.text()).toContain('发现 · 选择 · 下载')
  })

  it('guards protected routes and accepts only safe internal login redirects', async () => {
    const auth = useAuthStore()
    auth.principal = null

    await router.push('/login')
    await router.push('/library')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.redirect).toBe('/library')

    auth.principal = { username: 'admin-user', role: 'admin' }
    await router.push('/login?redirect=/configuration')
    expect(router.currentRoute.value.path).toBe('/configuration')

    await router.push('/login?redirect=https://example.invalid')
    expect(router.currentRoute.value.path).toBe('/library')
  })
})
