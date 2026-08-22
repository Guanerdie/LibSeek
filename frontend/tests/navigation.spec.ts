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
  it('keeps operational routes directly accessible while exposing only daily navigation', async () => {
    expect(router.resolve('/login').matched).toHaveLength(1)
    expect(router.resolve('/configuration').matched).toHaveLength(1)
    expect(router.resolve('/discovery').matched).toHaveLength(1)
    expect(router.resolve('/adapters').matched).toHaveLength(1)
    expect(router.resolve('/approvals/approval-1').matched).toHaveLength(1)
    expect(router.resolve('/qbittorrent').matched).toHaveLength(1)
    expect(router.resolve('/executions/execution-1').matched).toHaveLength(1)
    expect(router.resolve('/download-jobs/job-1').matched).toHaveLength(1)
    expect(router.resolve('/download-batches').matched).toHaveLength(1)
    expect(router.resolve('/download-batches/batch-1').matched).toHaveLength(1)
    expect(router.resolve('/media-imports').matched).toHaveLength(1)
    expect(router.resolve('/media-imports/new').matched).toHaveLength(1)
    expect(router.resolve('/media-imports/import-1').matched).toHaveLength(1)
    expect(router.resolve('/automation').matched).toHaveLength(1)
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/media')

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
      { href: '/media', text: '▦待处理' },
      { href: '/download-jobs', text: '↓下载' },
      { href: '/download-batches', text: '≡批次' },
      { href: '/configuration', text: '⚙设置' },
    ])
    expect(wrapper.find('.nav-disclosure').exists()).toBe(false)
    expect(wrapper.find('a[href="/"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/discovery"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/adapters"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/approvals"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/media-imports"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/automation"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/qbittorrent"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('admin-user')
    expect(wrapper.text()).toContain('管理员')
    expect(wrapper.text()).toContain('人工确认')
  })

  it('guards protected routes and accepts only safe internal login redirects', async () => {
    const auth = useAuthStore()
    auth.principal = null

    await router.push('/login')
    await router.push('/media')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.redirect).toBe('/media')

    auth.principal = { username: 'admin-user', role: 'admin' }
    await router.push('/login?redirect=/configuration')
    expect(router.currentRoute.value.path).toBe('/configuration')

    await router.push('/login?redirect=https://example.invalid')
    expect(router.currentRoute.value.path).toBe('/media')
  })
})
